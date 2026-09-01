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
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
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

from app.bot_texts import (
    ASK_EMAIL_TEXT,
    ASK_PASSWORD_TEXT,
    TERMS,
    TERMS_DECLINED,
    WELCOME,
)
from app.core.claude_client import ClaudeNotConfiguredError, chat_with_coach
from app.core.config import settings
from app.core.crypto import encrypt
from app.core.garmin_client import GarminAuthError, GarminRateLimitError, login_to_garmin
from app.db.session import engine
from app.models.garmin_account import GarminAccount
from app.models.user import User
from app.services.metrics_view import (
    format_last_activity,
    format_metrics_snapshot,
    format_recovery,
    format_resting_heart_rate,
    format_sleep,
    format_status,
    format_steps_today,
    format_week,
)
from app.tasks import backfill_user_history_task, sync_one_user_task

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWAITING_TERMS, ASK_EMAIL, ASK_PASSWORD = range(3)

# Terms acceptance timestamp, held in memory across the onboarding
# conversation and written to the User row only once linking succeeds -
# same reason no User row is created before then.
TERMS_ACCEPTED_KEY = "terms_accepted_at"

# Quick lookups - each one is a single DB read, no Claude involved.
BTN_HEART = "❤️ דופק מנוחה"
BTN_STEPS = "👟 צעדים היום"
BTN_SLEEP = "😴 שינה"
BTN_LAST_ACTIVITY = "🏃 האימון האחרון"
BTN_RECOVERY = "🔋 התאוששות"
BTN_WEEK = "📅 השבוע שלי"
BTN_METRICS = "📊 סיכום מלא"
BTN_SYNC = "🔄 סנכרון"
BTN_COACH = "💬 שיחה עם המאמן"
BTN_SETTINGS = "⚙️ הגדרות"
BTN_EXIT_CHAT = "⬅️ חזרה לתפריט"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_HEART, BTN_STEPS],
        [BTN_SLEEP, BTN_LAST_ACTIVITY],
        [BTN_RECOVERY, BTN_WEEK],
        [BTN_METRICS, BTN_SYNC],
        [BTN_COACH, BTN_SETTINGS],
    ],
    resize_keyboard=True,
)

# Shown only while in chat mode, so the way out is always one visible tap -
# a mode with no obvious exit is a mode users get stuck in.
CHAT_KEYBOARD = ReplyKeyboardMarkup([[BTN_EXIT_CHAT]], resize_keyboard=True)

# Key in context.user_data. Deliberately in-memory: if the bot restarts,
# the user simply lands back in menu mode, which is the safe default -
# they press 💬 again. Nothing is lost.
CHAT_MODE_KEY = "in_chat_mode"

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

    # Strip the menu keyboard for the duration of onboarding: at this point
    # the only valid actions are the two inline buttons, and leaving a
    # menu on screen would invite taps that can't be honored yet.
    await update.message.reply_text(WELCOME, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    await update.effective_chat.send_message(
        TERMS,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ מאשר", callback_data="terms:accept"),
                    InlineKeyboardButton("❌ לא מאשר", callback_data="terms:decline"),
                ]
            ]
        ),
    )
    return AWAITING_TERMS


async def on_terms_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "terms:decline":
        # Editing the original message removes the buttons, so a declined
        # prompt can't be re-answered from scrollback later.
        await query.edit_message_text(TERMS_DECLINED)
        return ConversationHandler.END

    context.user_data[TERMS_ACCEPTED_KEY] = datetime.now(timezone.utc)
    await query.edit_message_text("📋 תנאי השימוש אושרו ✅")
    await query.message.chat.send_message(ASK_EMAIL_TEXT, parse_mode="Markdown")
    return ASK_EMAIL


async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Held only in memory for the next message; never written to the DB
    # unless the login actually succeeds.
    context.user_data["garmin_email"] = update.message.text.strip()
    await update.message.reply_text(ASK_PASSWORD_TEXT, parse_mode="Markdown")
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
            user = User(
                telegram_chat_id=chat_id,
                is_active=True,
                terms_accepted_at=context.user_data.get(TERMS_ACCEPTED_KEY),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        elif user.terms_accepted_at is None:
            # Re-linking after an unlink still goes through the terms
            # screen, so record it if this is the first time we've captured it.
            user.terms_accepted_at = context.user_data.get(TERMS_ACCEPTED_KEY)
            session.add(user)
            session.commit()

        existing = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()
        if existing is not None:
            existing.encrypted_token = encrypt(token_bundle)
            existing.linked_at = datetime.now(timezone.utc)
            session.add(existing)
        else:
            session.add(GarminAccount(user_id=user.id, encrypted_token=encrypt(token_bundle)))
        session.commit()
        user_id = user.id

    # No menu keyboard yet - onboarding has one step left (the summary
    # hour), and the menu is what signals "setup is done".
    await update.effective_chat.send_message(
        "✅ *מעולה, החשבון חובר בהצלחה!*\n\nמושך עכשיו את הנתונים שלך מגרמין 🔄",
        parse_mode="Markdown",
    )
    # Two tasks on purpose: the quick one makes today's data available in
    # seconds (and reports back with the first metrics, so the user sees
    # something concrete right after linking), the backfill fills in a month
    # of history behind it.
    sync_one_user_task.delay(user_id, notify_on_success=True)
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


def _quick_lookup(formatter):
    """
    Builds a handler for a read-only DB lookup. All the quick buttons do
    exactly the same thing - check the account is linked, run one formatter,
    reply - so the shared shape lives here rather than being copy-pasted
    once per button.
    """

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        with Session(engine) as session:
            user = _find_user(session, update.effective_chat.id)
            if user is None or not _has_linked_garmin(session, user.id):
                await update.message.reply_text("צריך קודם לחבר חשבון גרמין - שלח /start 🔗")
                return
            text = formatter(session, user.id)
        # Re-attaching the menu on every reply means the keyboard can't get
        # lost: Telegram keeps whatever was last sent for that chat, so a
        # user who somehow cleared it gets it back on their next tap
        # instead of being stuck with a plain text box.
        await _reply(update, text, reply_markup=MAIN_KEYBOARD)

    return handler


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

    # notify_on_success: the task itself sends the refreshed metrics when it
    # finishes. Without it the user was left on "started in background" with
    # no completion message, which reads as the bot having hung.
    sync_one_user_task.delay(user_id, notify_on_success=True)
    await update.message.reply_text("🔄 מסנכרן מול גרמין… אשלח לך את המדדים המעודכנים בעוד כמה שניות.")


async def enter_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Explicit opt-in to free typing. Outside this mode typed text isn't sent
    anywhere, so the buttons stay the only way to interact - which is what
    keeps the bot predictable (and stops questions being typed at a coach
    that isn't connected yet).
    """
    with Session(engine) as session:
        user = _find_user(session, update.effective_chat.id)
        if user is None or not _has_linked_garmin(session, user.id):
            await update.message.reply_text("צריך קודם לחבר חשבון גרמין - שלח /start 🔗")
            return

    if not settings.is_claude_configured:
        # Don't put the user into a mode that can't answer them.
        await update.message.reply_text(
            "🤖 *המאמן החכם עדיין לא מחובר*\n\n"
            "חסר מפתח API - ברגע שיחובר תוכל לשוחח כאן חופשי על הנתונים שלך.\n"
            "בינתיים כל הכפתורים האחרים עובדים 👍",
            parse_mode="Markdown",
        )
        return

    context.user_data[CHAT_MODE_KEY] = True
    await update.message.reply_text(
        "💬 *נכנסת למצב שיחה*\n\n"
        "עכשיו אפשר לכתוב לי חופשי ואענה על סמך הנתונים האמיתיים שלך.\n\n"
        "דוגמאות:\n"
        "• _איך ישנתי השבוע?_\n"
        "• _כמה כדאי לי לרוץ מחר?_\n"
        "• _מה זה VO₂ max ומה המצב שלי?_\n"
        "• _תבנה לי תוכנית אימונים ל-10 ק\"מ_\n"
        "• _למה אני מרגיש עייף?_\n\n"
        "לחזרה לתפריט - לחץ ⬅️ חזרה לתפריט",
        parse_mode="Markdown",
        reply_markup=CHAT_KEYBOARD,
    )


async def exit_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[CHAT_MODE_KEY] = False
    await update.message.reply_text("חזרת לתפריט 👇", reply_markup=MAIN_KEYBOARD)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Explicit way to get the button menu back. Telegram only changes a
    chat's keyboard when a message carries a new one, so a user whose
    keyboard predates these buttons (or who hid it) needs some command
    that re-sends it - and /start is the wrong tool once already linked.
    """
    context.user_data[CHAT_MODE_KEY] = False
    await update.message.reply_text("👇 הנה התפריט", reply_markup=MAIN_KEYBOARD)


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
        await query.edit_message_text(f"🔔 מצוין! אשלח לך סיכום יומי כל יום ב-{hour:02d}:00.")

    # Sending the menu here is what ends onboarding: it only appears once
    # terms are accepted, Garmin is linked and the summary preference is
    # set, so its arrival is the signal that setup is complete.
    await query.message.chat.send_message(
        "🎉 *הכל מוכן!*\n\nאפשר להתחיל - בחר מה שתרצה מהתפריט שלמטה 👇",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
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
    """
    Handles typed text. Only acts when the user has explicitly entered chat
    mode; otherwise it nudges them back to the buttons rather than silently
    swallowing the message or spending tokens on something they may have
    typed by accident.
    """
    chat_id = update.effective_chat.id

    if not context.user_data.get(CHAT_MODE_KEY):
        await update.message.reply_text(
            "אני עובד עם הכפתורים שלמטה 👇\n"
            "כדי לשאול אותי שאלות חופשיות - לחץ על 💬 שיחה עם המאמן",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    with Session(engine) as session:
        user = _find_user(session, chat_id)
        if user is None or not _has_linked_garmin(session, user.id):
            await update.message.reply_text(
                "כדי שאוכל לענות על סמך הנתונים שלך, צריך קודם לחבר את חשבון הגרמין.\nשלח /start 🔗"
            )
            return

    if not settings.is_claude_configured:
        context.user_data[CHAT_MODE_KEY] = False
        await update.message.reply_text(
            "🤖 המאמן החכם עדיין לא מחובר (חסר מפתח API).",
            reply_markup=MAIN_KEYBOARD,
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
        context.user_data[CHAT_MODE_KEY] = False
        await update.message.reply_text(
            "🤖 המאמן החכם עדיין לא מחובר (חסר מפתח API).", reply_markup=MAIN_KEYBOARD
        )
        return
    except Exception:
        logger.exception("Coach chat failed for chat_id %s", chat_id)
        await update.message.reply_text("משהו השתבש אצלי 😕 נסה שוב בעוד רגע.")
        return

    # Keep the exit button on screen so the way out stays visible for as
    # long as the conversation runs.
    await _reply(update, reply, reply_markup=CHAT_KEYBOARD)


def build_application() -> Application:
    application = Application.builder().token(settings.telegram_bot_token).build()

    onboarding = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            # Only the inline buttons advance from here - typed text is
            # ignored until the terms are answered one way or the other.
            AWAITING_TERMS: [CallbackQueryHandler(on_terms_response, pattern=r"^terms:")],
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, do_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(onboarding)

    application.add_handler(CommandHandler("menu", show_menu))
    application.add_handler(CommandHandler("sync", sync_now))
    application.add_handler(CommandHandler("settings", show_settings))

    # Exact-text handlers for the menu buttons, registered before the
    # catch-all so a button tap is never mistaken for a typed question -
    # including while in chat mode, so the exit button always works.
    for button_text, handler in (
        (BTN_HEART, _quick_lookup(format_resting_heart_rate)),
        (BTN_STEPS, _quick_lookup(format_steps_today)),
        (BTN_SLEEP, _quick_lookup(format_sleep)),
        (BTN_LAST_ACTIVITY, _quick_lookup(format_last_activity)),
        (BTN_RECOVERY, _quick_lookup(format_recovery)),
        (BTN_WEEK, _quick_lookup(format_week)),
        (BTN_METRICS, _quick_lookup(format_metrics_snapshot)),
        (BTN_SYNC, sync_now),
        (BTN_COACH, enter_chat_mode),
        (BTN_SETTINGS, show_settings),
        (BTN_EXIT_CHAT, exit_chat_mode),
    ):
        application.add_handler(MessageHandler(filters.Text([button_text]), handler))

    application.add_handler(CallbackQueryHandler(on_summary_hour, pattern=r"^summary_hour:"))
    application.add_handler(CallbackQueryHandler(on_settings_action, pattern=r"^settings:"))

    # Typed text - answered by the coach only while in chat mode, otherwise
    # pointed back at the buttons.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, talk_to_coach))

    return application


async def _register_commands(application: Application) -> None:
    """
    Publishes the slash commands so they show up in Telegram's own "/" menu -
    a discoverable fallback for anyone whose reply keyboard is hidden.
    """
    await application.bot.set_my_commands(
        [
            ("menu", "הצג את תפריט הכפתורים"),
            ("sync", "סנכרון מול גרמין"),
            ("settings", "הגדרות"),
            ("start", "חיבור חשבון גרמין"),
        ]
    )


def main() -> None:
    application = build_application()
    application.post_init = _register_commands
    logger.info("Starting Telegram bot (long polling)")
    application.run_polling()


if __name__ == "__main__":
    main()
