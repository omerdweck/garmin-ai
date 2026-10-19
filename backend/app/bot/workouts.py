"""
Workouts in Telegram: the preview under a workout the coach built, its
buttons (to the watch, a file, discard), and the "🏋️ בנה אימון" builder.

Nothing here talks to Garmin. Sending, the .fit file and removing are
queued to the worker; only the trainer files (.zwo/.erg/.mrc), which are
pure text, are written right here.
"""

import logging
from datetime import datetime, timedelta

from sqlmodel import Session, col, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.common import BTN_BUILD_WORKOUT, CHAT_MODE_KEY, _find_user, _reply, _require_active  # noqa: F401
from app.core import clock
from app.core.config import settings
from app.db.session import engine
from app.models.workout import STATUS_DRAFT, StoredWorkout
from app.services.workout_files import FORMATS, FileError, available_formats
from app.services.workout_view import SPORT_LABELS, describe
from app.services.workouts import (
    WorkoutNotFound,
    can_send_to_watch,
    discard_draft,
    file_name,
    load_workout,
    user_ftp,
)

logger = logging.getLogger(__name__)

MAX_DAYS_AHEAD = 60


# --- the preview ---------------------------------------------------------------

def workout_keyboard(row: StoredWorkout, workout, user, ftp) -> InlineKeyboardMarkup:
    rows = []
    if can_send_to_watch(user, workout.sport):
        today = clock.today()
        days = [("היום", today), ("מחר", today + timedelta(days=1))]
        if workout.date and workout.date not in (d for _, d in days) and workout.date > today:
            days.append((workout.date.strftime("%d.%m"), workout.date))
        rows.append([
            InlineKeyboardButton(f"📲 לשעון: {label}", callback_data=f"wk:send:{row.id}:{day.isoformat()}")
            for label, day in days
        ])
    files = [InlineKeyboardButton("📄 .fit", callback_data=f"wk:file:{row.id}:fit")] if can_send_to_watch(
        user, workout.sport
    ) else []
    files += [
        InlineKeyboardButton(f"📄 .{fmt}", callback_data=f"wk:file:{row.id}:{fmt}")
        for fmt in available_formats(workout, ftp)
    ]
    if files:
        rows.append(files)
    rows.append([InlineKeyboardButton("🗑 לא צריך", callback_data=f"wk:drop:{row.id}")])
    return InlineKeyboardMarkup(rows)


def preview_text(workout, user) -> str:
    text = describe(workout)
    if not can_send_to_watch(user, workout.sport):
        text += "\n\n_שליחה לשעון לענף הזה עוד בבדיקה. בינתיים אפשר לראות את האימון כאן._"
    return text


async def show_new_workouts(update: Update, chat_id: int, since: datetime) -> None:
    """Every workout the coach built in the turn that just ended, each with its buttons."""
    with Session(engine) as session:
        user = _find_user(session, chat_id)
        if user is None:
            return
        rows = session.exec(
            select(StoredWorkout)
            .where(
                StoredWorkout.user_id == user.id,
                StoredWorkout.status == STATUS_DRAFT,
                StoredWorkout.created_at >= since,
            )
            .order_by(col(StoredWorkout.id))
        ).all()
        previews = []
        for row in rows:
            try:
                _, workout = load_workout(session, user.id, row.id)
            except WorkoutNotFound:
                continue
            previews.append(
                (preview_text(workout, user), workout_keyboard(row, workout, user, user_ftp(session, user.id)))
            )
    for text, keyboard in previews:
        await _reply(update, text, reply_markup=keyboard)


# --- the buttons ---------------------------------------------------------------

def _valid_day(iso: str):
    try:
        day = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return None
    today = clock.today()
    return day if today <= day <= today + timedelta(days=MAX_DAYS_AHEAD) else None


async def on_workout_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Imported here: tasks imports the bot-free services only, and the bot
    # must not drag Celery's task registry in at module load more than once.
    from app import tasks

    query = update.callback_query
    await query.answer()
    user_id = await _require_active(update)
    if user_id is None:
        return

    parts = (query.data or "").split(":")
    if len(parts) < 3 or not parts[2].isdigit():
        return
    action, workout_id = parts[1], int(parts[2])

    with Session(engine) as session:
        user = _find_user(session, update.effective_chat.id)
        try:
            row, workout = load_workout(session, user.id, workout_id)
        except WorkoutNotFound:
            await query.message.reply_text("האימון הזה כבר לא קיים.")
            return
        ftp = user_ftp(session, user.id)
        allowed = can_send_to_watch(user, workout.sport)

        if action == "drop":
            if row.garmin_workout_id is not None:
                tasks.remove_workout_task.delay(user.id, workout_id)
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text("⏳ מסיר מהלוח של Garmin...")
            else:
                discard_draft(session, user.id, workout_id)
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text("🗑 נמחק.")
            return

    if action == "send" and len(parts) == 4:
        day = _valid_day(parts[3])
        if day is None or not allowed:
            await query.message.reply_text("אי אפשר לשלוח את האימון הזה לתאריך הזה.")
            return
        tasks.send_workout_task.delay(user_id, workout_id, day.isoformat())
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("⏳ שולח לשעון...")
        return

    if action == "file" and len(parts) == 4:
        fmt = parts[3]
        if fmt == "fit":
            if not allowed:
                return
            tasks.workout_fit_task.delay(user_id, workout_id)
            await query.message.reply_text("⏳ מכין קובץ .fit...")
            return
        if fmt in FORMATS:
            try:
                content = FORMATS[fmt](workout, ftp)
            except FileError as exc:
                await query.message.reply_text(str(exc))
                return
            await update.effective_chat.send_document(
                document=content.encode("utf-8"), filename=file_name(row, fmt), caption=f"📄 {row.name}"
            )


# --- the builder ---------------------------------------------------------------

BUILDER_SPORTS = ["running", "cycling", "swimming", "strength", "walking", "hiit", "yoga", "mobility"]
DURATIONS = [20, 30, 45, 60, 90]
FOCUS = {
    "running": {"easy": "ריצה קלה", "intervals": "אינטרוולים", "tempo": "טמפו", "long": "ריצה ארוכה", "hills": "עליות"},
    "cycling": {"endurance": "סיבולת", "sweetspot": "סוויט-ספוט", "vo2": "VO2max", "recovery": "התאוששות"},
    "swimming": {"technique": "טכניקה", "endurance": "סיבולת", "speed": "מהירות"},
    "strength": {"full": "גוף מלא", "upper": "פלג גוף עליון", "lower": "רגליים", "core": "ליבה"},
    "walking": {"easy": "הליכה נינוחה", "brisk": "הליכה מהירה"},
    "hiit": {"general": "כללי"},
    "yoga": {"general": "כללי"},
    "mobility": {"general": "כללי"},
}


def _sport_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"{SPORT_LABELS[s][0]} {SPORT_LABELS[s][1]}", callback_data=f"wkb:s:{s}")
        for s in BUILDER_SPORTS
    ]
    return InlineKeyboardMarkup([buttons[i : i + 2] for i in range(0, len(buttons), 2)])


async def build_workout_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _require_active(update) is None:
        return
    if not settings.is_claude_configured:
        await update.message.reply_text("🤖 המאמן החכם לא מחובר כרגע, ולכן אי אפשר לבנות אימון.")
        return
    await update.message.reply_text("🏋️ *בניית אימון*\n\nאיזה אימון?", parse_mode="Markdown", reply_markup=_sport_keyboard())


def builder_request(sport: str, minutes: int, focus: str) -> str:
    """What the builder asks the coach - plain words, as if the athlete typed it."""
    label = SPORT_LABELS[sport][1]
    focus_text = FOCUS[sport][focus]
    return (
        f"תבנה לי אימון {label} של {minutes} דקות בערך, מיקוד: {focus_text}. "
        "תתאים אותו לנתונים ולתוכנית שלי, ותשמור אותו עם build_workout."
    )


async def on_builder_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.bot.coach import run_coach_turn

    query = update.callback_query
    await query.answer()
    if await _require_active(update) is None:
        return
    parts = (query.data or "").split(":")

    if len(parts) == 3 and parts[1] == "s" and parts[2] in FOCUS:
        sport = parts[2]
        await query.edit_message_text(
            f"{SPORT_LABELS[sport][0]} כמה זמן?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{m} דק'", callback_data=f"wkb:d:{sport}:{m}") for m in DURATIONS
            ]]),
        )
        return

    if len(parts) == 4 and parts[1] == "d" and parts[2] in FOCUS and parts[3].isdigit():
        sport, minutes = parts[2], parts[3]
        options = [
            InlineKeyboardButton(label, callback_data=f"wkb:f:{sport}:{minutes}:{key}")
            for key, label in FOCUS[sport].items()
        ]
        rows = [options[i : i + 2] for i in range(0, len(options), 2)]
        rows.append([InlineKeyboardButton("✍️ אכתוב בעצמי", callback_data="wkb:free")])
        await query.edit_message_text("מה המיקוד?", reply_markup=InlineKeyboardMarkup(rows))
        return

    if len(parts) == 5 and parts[1] == "f" and parts[2] in FOCUS and parts[4] in FOCUS[parts[2]]:
        sport, minutes, focus = parts[2], int(parts[3]), parts[4]
        if minutes not in DURATIONS:
            return
        await query.edit_message_text(f"🛠 בונה אימון {SPORT_LABELS[sport][1]}, {minutes} דק', {FOCUS[sport][focus]}...")
        context.user_data[CHAT_MODE_KEY] = True  # "make it shorter" goes straight to the coach
        await run_coach_turn(update, context, builder_request(sport, minutes, focus))
        return

    if parts == ["wkb", "free"]:
        context.user_data[CHAT_MODE_KEY] = True
        await query.edit_message_text(
            "✍️ תכתוב איזה אימון אתה רוצה, למשל: _6×800 בקצב 10 ק\"מ עם 2 דקות מנוחה_",
            parse_mode="Markdown",
        )
