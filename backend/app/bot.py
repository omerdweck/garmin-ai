"""
The Telegram bot - runs as its own process (docker-compose `bot`
service), separate from FastAPI and Celery. Uses long polling (the bot
actively asks Telegram "anything new?" in a loop) rather than webhooks,
since webhooks need a public HTTPS URL Telegram can reach and we don't
have one yet - switch to webhooks once there's a real deployment.

Interaction model: a persistent reply keyboard is the main menu, so the
common actions are one tap and nothing has to be memorized. Anything the
user types that *isn't* a menu button is treated as a message to the AI
coach - there's deliberately no "chat mode" to enter and exit, because a
mode is something users get stuck in.

Two things are intentionally kept cheap: the metrics/activities buttons
read straight from Postgres and never call Claude (no tokens spent
restating numbers we already have exactly), and Garmin credentials are
handled exactly once, at link time.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlmodel import Session, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.core.claude_client import ClaudeNotConfiguredError, chat_with_coach
from app.core.config import settings
from app.core.crypto import encrypt
from app.core.garmin_client import GarminAuthError, GarminRateLimitError, login_to_garmin
from app.db.session import engine
from app.models.garmin_account import GarminAccount
from app.models.user import User
from app.services.metrics_view import format_metrics_snapshot, format_recent_activities, format_status
from app.tasks import backfill_user_history_task, sync_one_user_task

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ASK_EMAIL, ASK_PASSWORD = range(2)

BTN_METRICS = "📊 המדדים שלי"
BTN_ACTIVITIES = "🏃 האימונים שלי"
BTN_SYNC = "🔄 סנכרון עכשיו"
BTN_COACH = "💬 שיחה עם המאמן"
BTN_SETTINGS = "⚙️ הגדרות"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_METRICS, BTN_ACTIVITIES], [BTN_SYNC, BTN_COACH], [BTN_SETTINGS]],
    resize_keyboard=True,
)

# Evening hours only - a "daily summary" is an end-of-day message, and a
# short list keeps the picker to one glance instead of 24 buttons.
SUMMARY_HOUR_CHOICES = [18, 19, 20, 21, 22, 23]


def _summary_hour_keyboard() -> InlineKeyboardMarkup:
    hour_buttons = [
        InlineKeyboardButton(f"{hour:02d}:00", callback_data=f"summary_hour:{hour}")
        for hour in SUMMARY_HOUR_CHOICES
    ]
    rows = [hour_buttons[i : i + 3] for i in range(0, len(hour_buttons), 3)]
    rows.append([InlineKeyboardButton("🔕 בלי סיכום יומי", callback_data="summary_hour:off")])
    return InlineKeyboardMarkup(rows)


def _settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔔 שינוי שעת הסיכום היומי", callback_data="settings:summary_hour")],
            [InlineKeyboardButton("🔌 ניתוק חשבון הגרמין", callback_data="settings:unlink")],
        ]
    )


async def _reply(update: Update, text: str, **kwargs) -> None:
    """
    Sends Markdown, falling back to plain text if Telegram rejects it.
    Claude's replies are free-form and can contain an unbalanced `*` or `_`
    that makes Telegram reject the whole message - losing the answer over a
    formatting character would be a much worse failure than losing the bold.
    """
    chat = update.effective_chat
    try:
        await chat.send_message(text, parse_mode="Markdown", **kwargs)
    except BadRequest:
        await chat.send_message(text, **kwargs)


def _find_user(session: Session, chat_id: int) -> User | None:
    return session.exec(select(User).where(User.telegram_chat_id == chat_id)).first()


def _has_linked_garmin(session: Session, user_id: int) -> bool:
    return session.exec(select(GarminAccount).where(GarminAccount.user_id == user_id)).first() is not None


# --------------------------------------------------------------------------
# Onboarding
# --------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id

    with Session(engine) as session:
        user = _find_user(session, chat_id)
        already_linked = user is not None and _has_linked_garmin(session, user.id)

    if already_linked:
        await update.message.reply_text(
            "שלום שוב! 👋 החשבון שלך כבר מחובר לגרמין.\nבחר פעולה מהתפריט למטה 👇",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "ברוך הבא ל-Garmin AI! 🏃‍♂️\n\n"
        "אני מאמן אישי שמנתח את נתוני שעון הגרמין שלך - שינה, דופק, HRV, אימונים ועוד.\n\n"
        "בוא נתחיל בחיבור החשבון.\n"
        "📧 מה כתובת המייל שאיתה אתה מתחבר לגרמין?\n\n"
        "_(אפשר לבטל בכל שלב עם /cancel)_",
        parse_mode="Markdown",
    )
    return ASK_EMAIL


async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Held only in memory for the next message; never written to the DB
    # unless the login actually succeeds.
    context.user_data["garmin_email"] = update.message.text.strip()
    await update.message.reply_text(
        "תודה! 🔒 עכשיו שלח את הסיסמה לגרמין.\n\n"
        "_ההודעה עם הסיסמה תימחק אוטומטית מיד אחרי שאקרא אותה, "
        "והסיסמה עצמה לא נשמרת אצלנו בשום מקום - רק אסימון גישה מוצפן._",
        parse_mode="Markdown",
    )
    return ASK_PASSWORD


async def do_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = context.user_data.pop("garmin_email", None)
    password = update.message.text
    chat_id = update.effective_chat.id

    # Delete the message carrying the password right away, success or not -
    # it must not sit around in the chat history.
    await update.message.delete()
    await update.effective_chat.send_message("🔄 מתחבר לגרמין… זה יכול לקחת עד כדקה.")

    try:
        token_bundle = await asyncio.to_thread(login_to_garmin, email, password)
    except GarminAuthError:
        await update.effective_chat.send_message(
            "❌ פרטי ההתחברות לגרמין שגויים.\nשלח /start כדי לנסות שוב."
        )
        return ConversationHandler.END
    except GarminRateLimitError:
        await update.effective_chat.send_message(
            "⏳ גרמין חוסמים כרגע ניסיונות התחברות (הגבלה זמנית מצדם).\n"
            "נסה שוב בעוד כמה דקות עם /start."
        )
        return ConversationHandler.END

    # Only now, after a *successful* login, does anything get persisted -
    # a failed attempt leaves no row behind to clutter the DB.
    with Session(engine) as session:
        user = _find_user(session, chat_id)
        if user is None:
            user = User(telegram_chat_id=chat_id, is_active=True)
            session.add(user)
            session.commit()
            session.refresh(user)

        existing = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()
        if existing is not None:
            existing.encrypted_token = encrypt(token_bundle)
            existing.linked_at = datetime.now(timezone.utc)
            session.add(existing)
        else:
            session.add(GarminAccount(user_id=user.id, encrypted_token=encrypt(token_bundle)))
        session.commit()
        user_id = user.id

    await update.effective_chat.send_message(
        "✅ מעולה, החשבון חובר בהצלחה!\n\n"
        "מושך עכשיו את החודש האחרון של הנתונים שלך ברקע 🔄\n"
        "_זה ייקח כדקה - בינתיים אפשר להתחיל לשחק עם התפריט._",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )
    # Two tasks on purpose: the quick one makes today's data available in
    # seconds, the backfill fills in a month of history behind it.
    sync_one_user_task.delay(user_id)
    backfill_user_history_task.delay(user_id)

    await update.effective_chat.send_message(
        "🔔 *דבר אחרון* - אני יכול לשלוח לך סיכום קצר בסוף כל יום: "
        "מה עשית, איך הגוף הגיב, ומה כדאי לתכנן למחר.\n\n"
        "באיזו שעה תרצה לקבל אותו?",
        parse_mode="Markdown",
        reply_markup=_summary_hour_keyboard(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("בוטל. שלח /start כדי להתחיל מחדש 🔄")
    return ConversationHandler.END


# --------------------------------------------------------------------------
# Menu actions
# --------------------------------------------------------------------------


async def show_metrics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with Session(engine) as session:
        user = _find_user(session, update.effective_chat.id)
        if user is None or not _has_linked_garmin(session, user.id):
            await update.message.reply_text("צריך קודם לחבר חשבון גרמין - שלח /start 🔗")
            return
        text = format_metrics_snapshot(session, user.id)
    await _reply(update, text)


async def show_activities(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with Session(engine) as session:
        user = _find_user(session, update.effective_chat.id)
        if user is None or not _has_linked_garmin(session, user.id):
            await update.message.reply_text("צריך קודם לחבר חשבון גרמין - שלח /start 🔗")
            return
        text = format_recent_activities(session, user.id)
    await _reply(update, text)


async def sync_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Enqueues the *same* Celery task the twice-daily schedule uses, rather
    than syncing inline: Garmin takes several seconds, and one shared code
    path means both routes get the same retry and reconnect-notification
    behavior.
    """
    with Session(engine) as session:
        user = _find_user(session, update.effective_chat.id)
        if user is None or not _has_linked_garmin(session, user.id):
            await update.message.reply_text("עדיין אין חשבון גרמין מקושר. שלח /start כדי לקשר אחד 🔗")
            return
        user_id = user.id

    sync_one_user_task.delay(user_id)
    await update.message.reply_text(
        "🔄 הסנכרון התחיל ברקע - זה ייקח כמה שניות.\n"
        "אחר כך תוכל ללחוץ על 📊 המדדים שלי כדי לראות את הנתונים המעודכנים."
    )


async def coach_intro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "💬 *דבר איתי חופשי!* פשוט תכתוב לי הודעה ואענה על סמך הנתונים האמיתיים שלך.\n\n"
        "כמה דוגמאות למה שאפשר לשאול:\n"
        "• _איך ישנתי השבוע?_\n"
        "• _כמה כדאי לי לרוץ מחר?_\n"
        "• _מה זה VO₂ max ומה המצב שלי?_\n"
        "• _תבנה לי תוכנית אימונים ל-10 ק\"מ_\n"
        "• _למה אני מרגיש עייף?_\n\n"
        "אין צורך ללחוץ על כלום - כל הודעה שתשלח מגיעה אליי 🙂",
        parse_mode="Markdown",
    )


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with Session(engine) as session:
        user = _find_user(session, update.effective_chat.id)
        if user is None:
            await update.message.reply_text("עדיין לא התחלנו - שלח /start 🔗")
            return
        text = format_status(session, user)
    await _reply(update, text, reply_markup=_settings_keyboard())


# --------------------------------------------------------------------------
# Inline button callbacks
# --------------------------------------------------------------------------


async def on_summary_hour(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]

    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        if user is None:
            await query.edit_message_text("צריך קודם לחבר חשבון - שלח /start 🔗")
            return
        user.daily_summary_hour = None if choice == "off" else int(choice)
        session.add(user)
        session.commit()
        hour = user.daily_summary_hour

    if hour is None:
        await query.edit_message_text("🔕 בסדר, לא אשלח סיכום יומי.\nתמיד אפשר להפעיל דרך ⚙️ הגדרות.")
    else:
        await query.edit_message_text(
            f"🔔 מצוין! אשלח לך סיכום יומי כל יום ב-{hour:02d}:00.\n\nשיהיה בהצלחה! 💪"
        )


async def on_settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]

    if action == "summary_hour":
        await query.message.reply_text(
            "🔔 באיזו שעה לשלוח את הסיכום היומי?",
            reply_markup=_summary_hour_keyboard(),
        )
        return

    if action == "unlink":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            if user is None:
                await query.edit_message_text("אין חשבון מקושר.")
                return
            account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()
            if account is not None:
                session.delete(account)
                session.commit()
        await query.edit_message_text(
            "🔌 חשבון הגרמין נותק.\nהנתונים ההיסטוריים שלך נשמרו.\nשלח /start כדי לחבר מחדש."
        )


# --------------------------------------------------------------------------
# Free-form chat with the AI coach
# --------------------------------------------------------------------------


async def talk_to_coach(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all for any text that isn't a menu button - routed to Claude."""
    chat_id = update.effective_chat.id

    with Session(engine) as session:
        user = _find_user(session, chat_id)
        if user is None or not _has_linked_garmin(session, user.id):
            await update.message.reply_text(
                "כדי שאוכל לענות על סמך הנתונים שלך, צריך קודם לחבר את חשבון הגרמין.\nשלח /start 🔗"
            )
            return

    if not settings.is_claude_configured:
        await update.message.reply_text(
            "🤖 המאמן החכם עדיין לא מחובר (חסר מפתח API).\n"
            "בינתיים אפשר להשתמש ב-📊 המדדים שלי וב-🏃 האימונים שלי."
        )
        return

    # Typing indicator - the Claude round-trip with tool calls takes a few
    # seconds, and silence reads as "broken".
    await update.effective_chat.send_action(ChatAction.TYPING)

    def _run() -> str:
        # A fresh session inside the worker thread: SQLModel/SQLAlchemy
        # sessions are not safe to share across threads.
        with Session(engine) as session:
            user = _find_user(session, chat_id)
            return chat_with_coach(session, user, update.message.text)

    try:
        reply = await asyncio.to_thread(_run)
    except ClaudeNotConfiguredError:
        await update.message.reply_text("🤖 המאמן החכם עדיין לא מחובר (חסר מפתח API).")
        return
    except Exception:
        logger.exception("Coach chat failed for chat_id %s", chat_id)
        await update.message.reply_text("משהו השתבש אצלי 😕 נסה שוב בעוד רגע.")
        return

    await _reply(update, reply)


def build_application() -> Application:
    application = Application.builder().token(settings.telegram_bot_token).build()

    onboarding = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, do_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(onboarding)

    application.add_handler(CommandHandler("sync", sync_now))
    application.add_handler(CommandHandler("settings", show_settings))

    # Exact-text handlers for the menu buttons, registered before the
    # catch-all so a button tap never gets sent to Claude as a question.
    for button_text, handler in (
        (BTN_METRICS, show_metrics),
        (BTN_ACTIVITIES, show_activities),
        (BTN_SYNC, sync_now),
        (BTN_COACH, coach_intro),
        (BTN_SETTINGS, show_settings),
    ):
        application.add_handler(MessageHandler(filters.Text([button_text]), handler))

    application.add_handler(CallbackQueryHandler(on_summary_hour, pattern=r"^summary_hour:"))
    application.add_handler(CallbackQueryHandler(on_settings_action, pattern=r"^settings:"))

    # Anything else the user types is a question for the coach.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, talk_to_coach))

    return application


def main() -> None:
    application = build_application()
    logger.info("Starting Telegram bot (long polling)")
    application.run_polling()


if __name__ == "__main__":
    main()
