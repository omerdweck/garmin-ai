"""
Shared pieces: menu buttons and keyboards, the account gate, reply helpers.

Moved verbatim from the former single-file app/bot.py.
"""
import logging
from typing import Optional

from sqlmodel import Session
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import BadRequest

from app.db.session import engine
from app.models.user import User
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.account_lifecycle import (
    AccountState,
    find_user_by_chat,
    resolve_state,
)


logger = logging.getLogger(__name__)

# Quick lookups - each one is a single DB read, no Claude involved.
BTN_ACTIVITIES = "🏃 האימונים שלי"
BTN_WEEK = "📅 השבוע שלי"
BTN_RACES = "🏁 תחזיות זמנים"
BTN_METRICS = "📊 המדדים שלי"
BTN_SYNC = "🔄 סנכרון"
BTN_ADD_ACTIVITY = "➕ הוספת אימון"
BTN_CALORIES = "🍽 קלוריות"
BTN_RECORDS = "🏆 השיאים שלי"
BTN_PLAN = "📋 התוכנית שלי"
BTN_COACH = "💬 שיחה עם המאמן"
BTN_SETTINGS = "⚙️ הגדרות"
BTN_EXIT_CHAT = "⬅️ חזרה לתפריט"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_METRICS, BTN_ACTIVITIES],
        [BTN_WEEK, BTN_RACES],
        [BTN_PLAN, BTN_CALORIES],
        [BTN_ADD_ACTIVITY, BTN_SYNC],
        [BTN_COACH, BTN_SETTINGS],
        [BTN_RECORDS],
    ],
    resize_keyboard=True,
)

# Every label on the persistent keyboard. Menu taps arrive as ordinary text
# messages, so any handler that consumes plain text has to be able to tell
# one apart from something the user typed.
MENU_BUTTONS = {
    BTN_METRICS, BTN_ACTIVITIES, BTN_WEEK, BTN_RACES, BTN_PLAN, BTN_CALORIES,
    BTN_RECORDS, BTN_ADD_ACTIVITY, BTN_SYNC, BTN_COACH, BTN_SETTINGS, BTN_EXIT_CHAT,
}

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
            [InlineKeyboardButton("📅 סיכום שבועי", callback_data="weekly:menu")],
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


async def _reply_plain(chat, text: str, **kwargs) -> None:
    try:
        await chat.send_message(text, parse_mode="Markdown", **kwargs)
    except BadRequest:
        await chat.send_message(text, **kwargs)


async def _reply_text(query, text: str) -> None:
    """edit_message_text with the same Markdown fallback _reply gives."""
    try:
        await query.edit_message_text(text, parse_mode="Markdown")
    except BadRequest:
        await query.edit_message_text(text)
