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
from typing import Optional

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
from app.core.claude_client import (
    ClaudeAuthError,
    ClaudeNotConfiguredError,
    ClaudeOutOfCreditError,
    ClaudeUnavailableError,
    chat_with_coach,
)
from app.core.config import settings
from app.core.crypto import encrypt
from app.core.garmin_client import GarminAuthError, GarminRateLimitError, login_to_garmin
from app.db.session import engine
from app.models.garmin_account import GarminAccount
from app.models.user import User
from app.models.activity import Activity
from app.services.admin_view import (
    format_admin_summary,
    format_admin_users,
    format_new_user_alert,
    set_user_limit,
)
from app.services.signup_control import (
    MAX_INVITE_ATTEMPTS,
    invite_code_matches,
    rejection_reason,
)
from app.services.usage_limits import QuotaExceeded, check_quota
from app.services.activity_view import (
    activity_type_counts,
    format_activity_detail,
    recent_of_type,
    summary_line,
    type_label,
)
from app.services.account_lifecycle import (
    AccountState,
    delete_user_completely,
    disconnect,
    find_user_by_chat,
    get_account,
    reconnect,
    resolve_state,
)
from app.services.garmin_sync import ensure_exercise_sets
from app.services.metrics_view import (
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

# httpx logs every request at INFO with the full URL - and Telegram puts the
# bot token *inside* the URL path (api.telegram.org/bot<TOKEN>/getMe). At the
# polling rate that writes the token to disk continuously, so anyone who can
# read the container logs owns the bot. WARNING keeps genuine transport
# failures visible while dropping the per-request lines that carry the token.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

ASK_INVITE, AWAITING_TERMS, ASK_EMAIL, ASK_PASSWORD = range(4)

# Counts wrong invite codes within one conversation. In user_data, so it
# resets if the bot restarts - acceptable, because the real ceiling on
# guessing is that a wrong code reveals nothing and the cap still applies.
INVITE_ATTEMPTS_KEY = "invite_attempts"

# Terms acceptance timestamp, held in memory across the onboarding
# conversation and written to the User row only once linking succeeds -
# same reason no User row is created before then.
TERMS_ACCEPTED_KEY = "terms_accepted_at"

# Quick lookups - each one is a single DB read, no Claude involved.
BTN_HEART = "❤️ דופק מנוחה"
BTN_STEPS = "👟 צעדים היום"
BTN_SLEEP = "😴 שינה"
BTN_ACTIVITIES = "🏃 האימונים שלי"
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
        [BTN_SLEEP, BTN_ACTIVITIES],
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
            [InlineKeyboardButton("🔌 ניתוק זמני (הנתונים נשמרים)", callback_data="settings:unlink")],
            [InlineKeyboardButton("🗑 מחיקת המשתמש והנתונים", callback_data="settings:delete")],
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
    return find_user_by_chat(session, chat_id)


def _reconnect_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔗 חבר מחדש", callback_data="account:reconnect")]])


# What to tell someone whose state doesn't permit the action they tried.
# Centralised so every entry point gives the same answer to the same
# situation instead of each handler inventing its own wording.
BLOCKED_MESSAGES = {
    AccountState.UNKNOWN: ("👋 שלח /start כדי להתחיל", None),
    AccountState.NEEDS_TERMS: (
        "📋 כדי להשתמש במערכת עליך לאשר את תנאי השימוש.\nשלח /start כדי לעבור עליהם ולאשר.",
        None,
    ),
    AccountState.NEEDS_LINK: (
        "🔗 חשבון הגרמין שלך אינו מחובר כרגע.\nשלח /start כדי לחבר אותו.",
        None,
    ),
    AccountState.DISCONNECTED: (
        "🔌 החשבון שלך מנותק כרגע.\nהנתונים שלך שמורים - לחיצה אחת ואתה חוזר לפעולה 👇",
        "reconnect",
    ),
}


async def _require_active(update: Update) -> Optional[int]:
    """
    Gate every action goes through. Returns the user id when the account is
    usable, otherwise replies with the right explanation for that state and
    returns None.
    """
    with Session(engine) as session:
        state, user = resolve_state(session, update.effective_chat.id)
        user_id = user.id if user else None

    if state is AccountState.ACTIVE:
        return user_id

    text, keyboard = BLOCKED_MESSAGES[state]
    await update.effective_chat.send_message(
        text,
        reply_markup=_reconnect_keyboard() if keyboard == "reconnect" else ReplyKeyboardRemove(),
    )
    return None


# --------------------------------------------------------------------------
# Onboarding
# --------------------------------------------------------------------------


def _terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ מאשר", callback_data="terms:accept"),
                InlineKeyboardButton("❌ לא מאשר", callback_data="terms:decline"),
            ]
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point for every state. Which of the five it lands in decides how
    much of onboarding actually runs - a returning user should never be
    asked to redo a step they've already completed.
    """
    with Session(engine) as session:
        state, _ = resolve_state(session, update.effective_chat.id)

    if state is AccountState.ACTIVE:
        await update.message.reply_text(
            "שלום שוב! 👋 החשבון שלך מחובר ומוכן.\nבחר פעולה מהתפריט למטה 👇",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    if state is AccountState.DISCONNECTED:
        await update.message.reply_text(
            "🔌 *החשבון שלך מנותק כרגע*\n\n"
            "כל הנתונים וההגדרות שלך שמורים - אין צורך להזין סיסמה מחדש.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        await update.effective_chat.send_message("מוכן לחזור? 👇", reply_markup=_reconnect_keyboard())
        return ConversationHandler.END

    if state is AccountState.NEEDS_LINK:
        # Terms already accepted and the user row already exists - the only
        # thing missing is a working credential, so skip straight to it.
        await update.message.reply_text(
            "🔗 *צריך לחבר מחדש את חשבון הגרמין*\n\n"
            "אנחנו זוכרים אותך ואת כל הנתונים שלך - רק ההתחברות לגרמין צריכה חידוש.\n\n"
            "📧 מה כתובת המייל שלך ב-Garmin Connect?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ASK_EMAIL

    # Strip the menu keyboard for the rest of onboarding: the only valid
    # actions from here are the inline buttons, and leaving a menu on
    # screen would invite taps that can't be honored yet.
    if state is AccountState.NEEDS_TERMS:
        await update.message.reply_text(
            "📋 *כדי להמשיך צריך לאשר את תנאי השימוש*\n\nאנחנו זוכרים אותך, רק האישור חסר.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        # AccountState.UNKNOWN - a genuinely new person, and the only path
        # that creates a user. Both gates apply here and nowhere else:
        # someone already registered must never be locked out by a code
        # change or a lowered cap.
        with Session(engine) as session:
            refusal = rejection_reason(session)
        if refusal is not None:
            await update.message.reply_text(refusal, reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

        if settings.is_invite_required:
            context.user_data[INVITE_ATTEMPTS_KEY] = 0
            await update.message.reply_text(
                "🔒 *הבוט הזה בהזמנה בלבד*\n\nשלח את קוד ההזמנה שקיבלת:",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove(),
            )
            return ASK_INVITE

        await update.message.reply_text(WELCOME, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())

    await update.effective_chat.send_message(TERMS, parse_mode="Markdown", reply_markup=_terms_keyboard())
    return AWAITING_TERMS


async def on_invite_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Checks the invite code, then hands over to the terms exactly as an open
    signup would - the gate adds a step, it does not change what follows.
    """
    chat_id = update.effective_chat.id

    if invite_code_matches(update.message.text or ""):
        logger.info("Invite code accepted for chat %s", chat_id)
        await update.message.reply_text(WELCOME, parse_mode="Markdown")
        await update.effective_chat.send_message(
            TERMS, parse_mode="Markdown", reply_markup=_terms_keyboard()
        )
        return AWAITING_TERMS

    attempts = context.user_data.get(INVITE_ATTEMPTS_KEY, 0) + 1
    context.user_data[INVITE_ATTEMPTS_KEY] = attempts
    logger.warning("Wrong invite code from chat %s (attempt %s)", chat_id, attempts)

    if attempts >= MAX_INVITE_ATTEMPTS:
        # Ending the conversation is the whole point: without a ceiling, a
        # short code is guessable at machine speed and the gate is decoration.
        await update.message.reply_text("❌ יותר מדי ניסיונות. פנה למי שהזמין אותך.")
        return ConversationHandler.END

    # Says nothing about how wrong the code was - no "close", no length hint.
    await update.message.reply_text(
        f"❌ קוד שגוי. נותרו {MAX_INVITE_ATTEMPTS - attempts} ניסיונות."
    )
    return ASK_INVITE


async def on_terms_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "terms:decline":
        # Editing the original message removes the buttons, so a declined
        # prompt can't be re-answered from scrollback later.
        await query.edit_message_text(TERMS_DECLINED)
        return ConversationHandler.END

    accepted_at = datetime.now(timezone.utc)
    context.user_data[TERMS_ACCEPTED_KEY] = accepted_at
    await query.edit_message_text("📋 תנאי השימוש אושרו ✅")

    # An already-known user (e.g. one who predates the terms screen) gets
    # the acceptance written immediately - there's no pending link step to
    # defer it to, and their Garmin may already be connected.
    with Session(engine) as session:
        state, user = resolve_state(session, query.message.chat_id)
        if user is not None and user.terms_accepted_at is None:
            user.terms_accepted_at = accepted_at
            session.add(user)
            session.commit()
        account_exists = user is not None and get_account(session, user.id) is not None

    if account_exists:
        await query.message.chat.send_message(
            "🎉 *הכל מוכן!*\n\nהחשבון שלך כבר מחובר - אפשר להתחיל 👇",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    await query.message.chat.send_message(ASK_EMAIL_TEXT, parse_mode="Markdown")
    return ASK_EMAIL


async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Held only in memory for the next message; never written to the DB
    # unless the login actually succeeds.
    # Lower-cased because phone keyboards auto-capitalise the first letter,
    # and the user shouldn't have to notice. Nothing here depends on the
    # original casing: this address is only ever sent to Garmin's login and
    # is never stored (the bot identifies people by telegram_chat_id).
    context.user_data["garmin_email"] = update.message.text.strip().lower()
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

    # Tell the owner immediately. Finding out from the monthly invoice that
    # strangers have been using the bot is exactly the situation the usage
    # tracking exists to prevent, and a ceiling nobody looks at is not a
    # control. Best-effort: a failed notification must not derail a
    # successful signup.
    if settings.is_admin_configured and settings.admin_chat_id != update.effective_chat.id:
        try:
            with Session(engine) as admin_session:
                new_user = admin_session.get(User, user_id)
                await context.bot.send_message(
                    settings.admin_chat_id,
                    format_new_user_alert(admin_session, new_user),
                    parse_mode="Markdown",
                )
        except Exception:
            logger.warning("Could not notify admin of new user %s", user_id, exc_info=True)

    await update.effective_chat.send_message(
        "🔔 *דבר אחרון* - אני יכול לשלוח לך סיכום קצר בסוף כל יום: "
        "מה עשית, איך הגוף הגיב, ומה כדאי לתכנן למחר.\n\n"
        "באיזו שעה תרצה לקבל אותו?",
        parse_mode="Markdown",
        reply_markup=_summary_hour_keyboard(),
    )
    return ConversationHandler.END


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Owner-only usage reporting and user management.

    A non-owner gets silence, not a refusal: "you are not authorised" would
    confirm the command exists and invite probing. As far as anyone else is
    concerned there is no /admin. For the same reason it is left out of
    set_my_commands, so it never appears in anyone's command menu.
    """
    chat_id = update.effective_chat.id
    if not settings.is_admin_configured or chat_id != settings.admin_chat_id:
        logger.info("Ignoring /admin from non-admin chat %s", chat_id)
        return

    args = context.args or []

    with Session(engine) as session:
        if not args:
            text = format_admin_summary(session)

        elif args[0] == "users":
            text = format_admin_users(session)

        elif args[0] == "limit" and len(args) == 3:
            try:
                text = set_user_limit(session, int(args[1]), int(args[2]))
            except ValueError:
                text = "❌ שימוש: `/admin limit <user_id> <מספר>`"

        elif args[0] == "delete" and len(args) == 2:
            # No confirmation step here on purpose: this is a typed command
            # available to one chat, and the destructive path users reach
            # through the menu already has its own two-button confirmation.
            try:
                target = int(args[1])
            except ValueError:
                text = "❌ שימוש: `/admin delete <user_id>`"
            else:
                if target == update.effective_user.id:
                    text = "❌ לא ניתן למחוק את חשבון המנהל דרך הפקודה הזו."
                elif session.get(User, target) is None:
                    text = f"❌ אין משתמש {target}"
                else:
                    delete_user_completely(session, target)
                    text = f"🗑 משתמש {target} נמחק על כל נתוניו."
        else:
            text = (
                "*פקודות ניהול*\n"
                "`/admin` - סיכום\n"
                "`/admin users` - רשימת משתמשים\n"
                "`/admin limit <id> <n>` - מכסה יומית (0 = חסימה)\n"
                "`/admin delete <id>` - מחיקה מלאה"
            )

    await update.message.reply_text(text, parse_mode="Markdown")


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
        user_id = await _require_active(update)
        if user_id is None:
            return
        with Session(engine) as session:
            text = formatter(session, user_id)
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
    user_id = await _require_active(update)
    if user_id is None:
        return

    # notify_on_success: the task itself sends the refreshed metrics when it
    # finishes. Without it the user was left on "started in background" with
    # no completion message, which reads as the bot having hung.
    sync_one_user_task.delay(user_id, notify_on_success=True)
    await update.message.reply_text("🔄 מסנכרן מול גרמין… אשלח לך את המדדים המעודכנים בעוד כמה שניות.")


async def show_activity_types(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    First level of the workouts browser: which kinds of training this user
    actually does. Only types they have are listed - a menu offering
    activities you've never done is noise, not choice.
    """
    user_id = await _require_active(update)
    if user_id is None:
        return

    with Session(engine) as session:
        counts = activity_type_counts(session, user_id)

    if not counts:
        await update.message.reply_text(
            "עדיין לא נמצאו אימונים 🤔\nנסה ללחוץ על 🔄 סנכרון.", reply_markup=MAIN_KEYBOARD
        )
        return

    rows = [
        [InlineKeyboardButton(f"{type_label(t)} ({n})", callback_data=f"acttype:{t}")]
        for t, n in counts
    ]
    await update.message.reply_text(
        "🏃 *האימונים שלך*\n\nבחר סוג אימון כדי לראות את הנתונים שלו 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def on_activity_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Second level: the individual workouts of the chosen type."""
    query = update.callback_query
    await query.answer()
    activity_type = query.data.split(":", 1)[1]

    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        if user is None:
            await query.edit_message_text("שלח /start כדי להתחיל 👋")
            return
        activities = recent_of_type(session, user.id, activity_type)
        rows = [
            [InlineKeyboardButton(summary_line(a), callback_data=f"act:{a.id}")] for a in activities
        ]

    rows.append([InlineKeyboardButton("⬅️ סוגי אימון", callback_data="acttype_menu")])
    await query.edit_message_text(
        f"{type_label(activity_type)}\n\nבחר אימון כדי לראות את כל הנתונים 👇",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def on_activity_types_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        counts = activity_type_counts(session, user.id) if user else []

    rows = [
        [InlineKeyboardButton(f"{type_label(t)} ({n})", callback_data=f"acttype:{t}")]
        for t, n in counts
    ]
    await query.edit_message_text(
        "🏃 בחר סוג אימון 👇", reply_markup=InlineKeyboardMarkup(rows)
    )


async def on_activity_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Third level: everything Garmin recorded for one workout."""
    query = update.callback_query
    await query.answer()
    activity_id = int(query.data.split(":", 1)[1])

    with Session(engine) as session:
        activity = session.get(Activity, activity_id)
        if activity is None:
            await query.edit_message_text("האימון לא נמצא 🤔")
            return
        user = _find_user(session, query.message.chat_id)
        if user is None or activity.user_id != user.id:
            # Callback data is client-supplied, so ownership is re-checked
            # here rather than trusted from the button.
            await query.edit_message_text("האימון לא נמצא 🤔")
            return
        activity_type = activity.activity_type
        needs_sets = activity_type == "strength_training" and activity.exercise_sets is None

    if needs_sets:
        await query.edit_message_text("🔄 טוען את פרטי התרגילים…")

        def _load():
            with Session(engine) as session:
                activity = session.get(Activity, activity_id)
                ensure_exercise_sets(session, activity)
                return format_activity_detail(activity)

        text = await asyncio.to_thread(_load)
    else:
        with Session(engine) as session:
            text = format_activity_detail(session.get(Activity, activity_id))

    back = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ חזרה", callback_data=f"acttype:{activity_type}")]]
    )
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back)
    except BadRequest:
        await query.edit_message_text(text, reply_markup=back)


async def enter_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Explicit opt-in to free typing. Outside this mode typed text isn't sent
    anywhere, so the buttons stay the only way to interact - which is what
    keeps the bot predictable (and stops questions being typed at a coach
    that isn't connected yet).
    """
    if await _require_active(update) is None:
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
            disconnect(session, user.id)

        await query.edit_message_text(
            "🔌 *החשבון נותק*\n\n"
            "הסנכרון האוטומטי והסיכום היומי הופסקו.\n"
            "כל הנתונים, ההגדרות וההתחברות שלך *נשמרו* - חזרה היא לחיצה אחת, בלי סיסמה.",
            parse_mode="Markdown",
        )
        await query.message.chat.send_message(
            "רוצה לחזור? 👇", reply_markup=_reconnect_keyboard()
        )
        return

    if action == "delete":
        await query.message.chat.send_message(
            "⚠️ *מחיקת המשתמש*\n\n"
            "הפעולה תמחק לצמיתות את *כל* המידע שלך:\n"
            "• כל נתוני הבריאות והאימונים שנאספו\n"
            "• היסטוריית השיחות עם המאמן\n"
            "• החיבור לחשבון הגרמין וההגדרות שלך\n\n"
            "*לא ניתן לשחזר.* אם תתחבר שוב בעתיד, תיקלט כמשתמש חדש לגמרי.\n\n"
            "האם אתה בטוח?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🗑 ברצוני למחוק", callback_data="account:delete_confirm")],
                    [InlineKeyboardButton("↩️ איני רוצה למחוק", callback_data="account:delete_cancel")],
                ]
            ),
        )


async def on_account_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    chat_id = query.message.chat_id

    if action == "reconnect":
        with Session(engine) as session:
            user = _find_user(session, chat_id)
            if user is None:
                await query.edit_message_text("שלח /start כדי להתחיל 👋")
                return
            user_id = user.id
            already_current = reconnect(session, user_id)

        await query.edit_message_text("✅ חזרת לפעולה!")
        if already_current:
            # Data already synced today - re-fetching it would spend time
            # and Garmin rate-limit budget to learn nothing new.
            await query.message.chat.send_message(
                "הנתונים שלך כבר מעודכנים להיום, אז אפשר להתחיל מיד 👇",
                reply_markup=MAIN_KEYBOARD,
            )
        else:
            sync_one_user_task.delay(user_id, notify_on_success=True)
            await query.message.chat.send_message(
                "🔄 מסנכרן את הנתונים העדכניים… אשלח לך אותם עוד רגע.",
                reply_markup=MAIN_KEYBOARD,
            )
        return

    if action == "delete_cancel":
        await query.edit_message_text("👍 לא נמחק כלום. החשבון שלך נשאר כמו שהוא.")
        return

    if action == "delete_confirm":
        with Session(engine) as session:
            user = _find_user(session, chat_id)
            if user is None:
                await query.edit_message_text("לא נמצא משתמש למחיקה.")
                return
            delete_user_completely(session, user.id)

        context.user_data.clear()
        await query.edit_message_text(
            "🗑 *הכל נמחק*\n\nכל המידע שלך הוסר מהמערכת לצמיתות.\n\n"
            "תודה שניסית! אם תרצה לחזור מתישהו - שלח /start ונתחיל מאפס.",
            parse_mode="Markdown",
        )
        # Clear the menu too: leaving it up would offer actions that now
        # resolve to "unknown user".
        await query.message.chat.send_message("👋", reply_markup=ReplyKeyboardRemove())


# --------------------------------------------------------------------------
# Free-form chat with the AI coach
# --------------------------------------------------------------------------


async def _keep_typing(chat) -> None:
    """
    Holds the "typing…" indicator up for as long as the caller needs.

    Telegram expires a chat action after roughly five seconds, so showing
    it for the length of a real answer means re-sending it on a timer.
    Cancelled by the caller once the reply is ready; a failure to send
    (network blip) must never take down the answer itself, so it's
    swallowed.
    """
    try:
        while True:
            try:
                await chat.send_action(ChatAction.TYPING)
            except Exception:
                logger.debug("typing indicator failed", exc_info=True)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


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

    if await _require_active(update) is None:
        context.user_data[CHAT_MODE_KEY] = False
        return

    if not settings.is_claude_configured:
        context.user_data[CHAT_MODE_KEY] = False
        await update.message.reply_text(
            "🤖 המאמן החכם עדיין לא מחובר (חסר מפתח API).",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    def _run() -> str:
        # A fresh session inside the worker thread: SQLModel/SQLAlchemy
        # sessions are not safe to share across threads.
        with Session(engine) as session:
            user = _find_user(session, chat_id)
            # Before the call, not after: a ceiling enforced afterwards has
            # already spent the money it exists to protect.
            check_quota(session, user)
            return chat_with_coach(session, user, update.message.text)

    # Typing runs for as long as the answer takes. A single send_action
    # only holds the indicator for about five seconds, and a tool-using
    # answer regularly takes far longer - so one call left the user
    # staring at silence, which is exactly what "is this thing broken?"
    # feels like.
    typing = asyncio.create_task(_keep_typing(update.effective_chat))
    try:
        reply = await asyncio.to_thread(_run)
    except QuotaExceeded as exc:
        # Not an error: the ceiling did its job. Leave chat mode on so the
        # user can keep the thread when the window resets, and keep the menu
        # attached - the free buttons genuinely still work, and saying so
        # turns a refusal into a redirect.
        logger.info("Quota reached for chat_id %s", chat_id)
        await update.message.reply_text(
            exc.message,
            reply_markup=MAIN_KEYBOARD,
        )
        return
    except ClaudeNotConfiguredError:
        context.user_data[CHAT_MODE_KEY] = False
        await update.message.reply_text(
            "🤖 *המאמן לא מחובר*\n\nחסר מפתח API. שאר הכפתורים עובדים כרגיל.",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    except ClaudeOutOfCreditError:
        logger.error("Anthropic credit exhausted (chat_id %s)", chat_id)
        await update.message.reply_text(
            "💳 *נגמר האשראי במנוי ה-AI*\n\n"
            "אני לא יכול לענות עד שתיטען יתרה בחשבון Anthropic.\n"
            "בינתיים כל שאר הכפתורים עובדים - הנתונים שלך זמינים כרגיל.",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    except ClaudeAuthError:
        logger.error("Anthropic key rejected (chat_id %s)", chat_id)
        await update.message.reply_text(
            "🔑 *מפתח ה-AI נדחה*\n\n"
            "ייתכן שהמפתח בוטל או הוחלף. צריך לעדכן אותו בהגדרות המערכת.\n"
            "שאר הכפתורים עובדים כרגיל.",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    except ClaudeUnavailableError:
        logger.warning("Anthropic temporarily unavailable (chat_id %s)", chat_id)
        await update.message.reply_text(
            "📡 *שירות ה-AI לא זמין כרגע*\n\nזו תקלה זמנית - נסה שוב בעוד דקה.",
            parse_mode="Markdown",
        )
        return
    except Exception:
        logger.exception("Coach chat failed for chat_id %s", chat_id)
        await update.message.reply_text("משהו השתבש אצלי 😕 נסה שוב בעוד רגע.")
        return
    finally:
        # Always stop the indicator, including on every error path above -
        # otherwise it keeps typing forever after a failure.
        typing.cancel()

    # Keep the exit button on screen so the way out stays visible for as
    # long as the conversation runs.
    await _reply(update, reply, reply_markup=CHAT_KEYBOARD)


def build_application() -> Application:
    application = Application.builder().token(settings.telegram_bot_token).build()

    onboarding = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_INVITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_invite_code)],
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
    # Deliberately absent from set_my_commands below - see admin().
    application.add_handler(CommandHandler("admin", admin))

    # Exact-text handlers for the menu buttons, registered before the
    # catch-all so a button tap is never mistaken for a typed question -
    # including while in chat mode, so the exit button always works.
    for button_text, handler in (
        (BTN_HEART, _quick_lookup(format_resting_heart_rate)),
        (BTN_STEPS, _quick_lookup(format_steps_today)),
        (BTN_SLEEP, _quick_lookup(format_sleep)),
        (BTN_ACTIVITIES, show_activity_types),
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
    application.add_handler(CallbackQueryHandler(on_account_action, pattern=r"^account:"))
    # Ordered narrowest-first: "acttype_menu" would otherwise be swallowed
    # by the broader "acttype:" pattern.
    application.add_handler(CallbackQueryHandler(on_activity_types_menu, pattern=r"^acttype_menu$"))
    application.add_handler(CallbackQueryHandler(on_activity_type, pattern=r"^acttype:"))
    application.add_handler(CallbackQueryHandler(on_activity_detail, pattern=r"^act:"))

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
