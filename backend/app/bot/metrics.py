"""
The metrics buttons and quick lookups - straight from the database, no AI.

Moved verbatim from the former single-file app/bot.py.
"""

from sqlmodel import Session
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    ContextTypes,
)

from app.db.session import engine
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.metrics_view import (
    format_metrics_snapshot,
    format_recovery,
    format_resting_heart_rate,
    format_sleep,
    format_steps_today,
)
from app.tasks import sync_one_user_task
from app.bot.common import MAIN_KEYBOARD, _find_user, _reply, _require_active



# --------------------------------------------------------------------------
# Menu actions
# --------------------------------------------------------------------------


# The four numbers that used to have a keyboard button each. The snapshot
# already shows all of them, so these are for reading one in detail - a
# drill-down, not a shortcut.
METRIC_DRILLDOWNS = [
    ("heart", "❤️ דופק מנוחה"),
    ("steps", "👟 צעדים"),
    ("sleep", "😴 שינה"),
    ("recovery", "🔋 התאוששות"),
]


def _metrics_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(label, callback_data=f"metric:{key}")
        for key, label in METRIC_DRILLDOWNS
    ]
    return InlineKeyboardMarkup([buttons[i : i + 2] for i in range(0, len(buttons), 2)])


async def show_metrics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    The full snapshot, with the individual metrics one tap below it.

    Four separate keyboard buttons became one because the snapshot already
    contained all four numbers - the buttons were shortcuts to a subset of a
    screen people were pressing anyway, and each cost a permanent slot on a
    keyboard that had grown to fourteen.
    """
    user_id = await _require_active(update)
    if user_id is None:
        return
    with Session(engine) as session:
        text = format_metrics_snapshot(session, user_id)
    await _reply(update, text, reply_markup=MAIN_KEYBOARD)
    await update.effective_chat.send_message(
        "לפירוט על מדד מסוים 👇", reply_markup=_metrics_keyboard()
    )


async def on_metric_drilldown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    key = query.data.split(":", 1)[1]

    formatters = {
        "heart": format_resting_heart_rate,
        "steps": format_steps_today,
        "sleep": format_sleep,
        "recovery": format_recovery,
    }
    formatter = formatters.get(key)
    if formatter is None:
        return

    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        if user is None:
            return
        text = formatter(session, user.id)

    # A new message rather than editing: the drill-downs are meant to be
    # tapped one after another, and editing would erase the previous answer
    # each time.
    try:
        await query.message.chat.send_message(text, parse_mode="Markdown")
    except BadRequest:
        await query.message.chat.send_message(text)


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
