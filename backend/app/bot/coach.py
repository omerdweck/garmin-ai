"""
Free-form conversation with the AI coach, and entering/leaving chat mode.

Moved verbatim from the former single-file app/bot.py.
"""
import asyncio
import logging

from sqlmodel import Session
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    ContextTypes,
)

from app.bot_concurrency import coach_executor
from app.core.claude_client import (
    ClaudeAuthError,
    ClaudeNotConfiguredError,
    ClaudeOutOfCreditError,
    ClaudeUnavailableError,
    chat_with_coach,
)
from app.core.config import settings
from app.db.session import engine
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.usage_limits import QuotaExceeded, check_quota
from app.bot.calories import CALORIE_KEY
from app.bot.common import CHAT_KEYBOARD, CHAT_MODE_KEY, MAIN_KEYBOARD, _find_user, _reply, _require_active
from app.bot.manual_activity import MANUAL_KEY

logger = logging.getLogger(__name__)



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
    # Also the escape hatch from a half-finished manual entry. Commands skip
    # the text handler entirely, so without clearing the draft here it would
    # sit in memory and swallow the next thing the user typed.
    context.user_data.pop(MANUAL_KEY, None)
    context.user_data.pop(CALORIE_KEY, None)
    await update.message.reply_text("👇 הנה התפריט", reply_markup=MAIN_KEYBOARD)


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
        reply, tools_used = await asyncio.get_running_loop().run_in_executor(coach_executor, _run)
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

    # A plan the coach just drafted is a proposal, not the user's plan. The
    # approve button is what moves it into tracking - keyed off the tool
    # actually having run rather than off anything in the reply text, which
    # the model is free to word however it likes.
    if "set_training_plan" in tools_used:
        await update.effective_chat.send_message(
            "התוכנית הזו עדיין *לא* נכנסה למעקב.\n"
            "אפשר לבקש שינויים, או לאשר אותה 👇",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ אישור התוכנית", callback_data="plan:approve"),
                        InlineKeyboardButton("🗑 לא, תודה", callback_data="plan:discard"),
                    ]
                ]
            ),
        )
