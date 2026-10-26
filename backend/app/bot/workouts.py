"""
Workouts in Telegram: the preview under a workout the coach built, choosing
its day, saving it in the bot or sending it to the watch, files - and the
workout builder, reached from "➕ הוספת אימון" → "🏋️ אימון חדש עם המאמן".

Nothing here talks to Garmin. Sending, the .fit file and removing are
queued to the worker; only the trainer files (.zwo/.erg/.mrc), which are
pure text, are written right here.

How a workout moves:
    draft ──📅 day──► confirm ──💾──► planned (in the bot, gets a reminder)
                               └─📲──► sent (on the Garmin calendar)
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import Session, col, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.bot.common import CHAT_MODE_KEY, _find_user, _reply, _require_active  # noqa: F401
from app.core import clock
from app.db.session import engine
from app.models.plan_session import DAY_NAMES_HE, week_index
from app.models.workout import STATUS_DRAFT, STATUS_PLANNED, STATUS_SENT, StoredWorkout
from app.services.manual_activity import parse_distance, parse_duration
from app.services.plan_view import day_label
from app.services.workout_files import FORMATS, FileError, available_formats
from app.services.workout_view import SPORT_LABELS, describe
from app.services.workouts import (
    WorkoutNotFound,
    can_send_to_watch,
    discard_draft,
    file_name,
    load_workout,
    plan_workout,
    take_over_from_old,
    user_ftp,
)

logger = logging.getLogger(__name__)

MAX_DAYS_AHEAD = 60
DAYS_IN_PICKER = 7


# --- days in callback data -------------------------------------------------------
# "20261023", or "0" for "no day chosen" - short, because callback data is
# limited to 64 bytes and the builder carries sport, amount and focus too.

def encode_day(day: Optional[date]) -> str:
    return day.strftime("%Y%m%d") if day else "0"


def decode_day(text: str) -> Optional[date]:
    """A day within the bookable window, or None for '0' and anything invalid."""
    if not text or text == "0":
        return None
    try:
        day = datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None
    today = clock.today()
    return day if today <= day <= today + timedelta(days=MAX_DAYS_AHEAD) else None


def _rows(buttons: list, width: int) -> list[list]:
    return [buttons[i : i + width] for i in range(0, len(buttons), width)]


# --- the preview and its keyboard --------------------------------------------------

def _file_buttons(row: StoredWorkout, workout, user, ftp) -> list:
    buttons = []
    if can_send_to_watch(user, workout.sport):
        buttons.append(InlineKeyboardButton("📄 .fit", callback_data=f"wk:file:{row.id}:fit"))
    buttons += [
        InlineKeyboardButton(f"📄 .{fmt}", callback_data=f"wk:file:{row.id}:{fmt}")
        for fmt in available_formats(workout, ftp)
    ]
    return buttons


def _confirm_rows(row: StoredWorkout, workout, user, day: date) -> list[list]:
    """The two ways to keep a workout for `day`, written out on the buttons themselves."""
    label = day_label(day)
    code = encode_day(day)
    allowed = can_send_to_watch(user, workout.sport)
    if row.status == STATUS_SENT:
        # Already on the watch: moving it must move it there too.
        return [[InlineKeyboardButton(f"📲 העבר בשעון ל{label}", callback_data=f"wk:send:{row.id}:{code}")]]
    buttons = [InlineKeyboardButton(f"💾 שמור ל{label}", callback_data=f"wk:save:{row.id}:{code}")]
    if allowed:
        buttons.append(InlineKeyboardButton("📲 שמור ושלח לשעון", callback_data=f"wk:send:{row.id}:{code}"))
    return [buttons]


def picker_keyboard(row: StoredWorkout) -> InlineKeyboardMarkup:
    today = clock.today()
    days = [today + timedelta(days=offset) for offset in range(DAYS_IN_PICKER)]
    buttons = [
        InlineKeyboardButton(day_label(day, today), callback_data=f"wk:pick:{row.id}:{encode_day(day)}")
        for day in days
    ]
    rows = _rows(buttons, 4)
    rows.append([InlineKeyboardButton("↩️ חזרה", callback_data=f"wk:back:{row.id}")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(row: StoredWorkout, workout, user, day: date) -> InlineKeyboardMarkup:
    rows = _confirm_rows(row, workout, user, day)
    rows.append([InlineKeyboardButton("↩️ יום אחר", callback_data=f"wk:day:{row.id}")])
    return InlineKeyboardMarkup(rows)


def workout_keyboard(row: StoredWorkout, workout, user, ftp) -> InlineKeyboardMarkup:
    """The buttons under a workout, for whatever state it is in now."""
    today = clock.today()
    rows: list[list] = []

    if row.status == STATUS_DRAFT:
        if workout.date and workout.date >= today:
            # The coach already said which day (or the builder was opened from
            # that day): offer it straight away instead of asking again.
            rows += _confirm_rows(row, workout, user, workout.date)
            rows.append([InlineKeyboardButton("📅 יום אחר", callback_data=f"wk:day:{row.id}")])
        else:
            rows.append([InlineKeyboardButton("📅 קביעת יום", callback_data=f"wk:day:{row.id}")])
        drop = "🗑 לא צריך"
    elif row.status == STATUS_PLANNED:
        actions = []
        if can_send_to_watch(user, workout.sport) and row.scheduled_for and row.scheduled_for >= today:
            actions.append(InlineKeyboardButton(
                "📲 שלח לשעון", callback_data=f"wk:send:{row.id}:{encode_day(row.scheduled_for)}"
            ))
        actions.append(InlineKeyboardButton("📅 שינוי יום", callback_data=f"wk:day:{row.id}"))
        rows.append(actions)
        drop = "🗑 מחיקה"
    else:
        rows.append([InlineKeyboardButton("📅 העבר יום", callback_data=f"wk:day:{row.id}")])
        drop = "🗑 מחיקה (גם מהשעון)"

    if row.status in (STATUS_PLANNED, STATUS_SENT):
        rows.append([InlineKeyboardButton("✏️ שינוי עם המאמן", callback_data=f"wk:edit:{row.id}")])

    files = _file_buttons(row, workout, user, ftp)
    if files:
        rows.append(files)
    # A kept workout is asked about before it goes; a draft nobody kept is not.
    drop_action = "drop" if row.status == STATUS_DRAFT else "del"
    rows.append([InlineKeyboardButton(drop, callback_data=f"wk:{drop_action}:{row.id}")])
    return InlineKeyboardMarkup(rows)


def delete_confirm_keyboard(row: StoredWorkout) -> InlineKeyboardMarkup:
    yes = "🗑 כן, למחוק גם מהשעון" if row.status == STATUS_SENT else "🗑 כן, למחוק"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(yes, callback_data=f"wk:drop:{row.id}"),
        InlineKeyboardButton("↩️ ביטול", callback_data=f"wk:back:{row.id}"),
    ]])


def preview_text(row: StoredWorkout, workout, user) -> str:
    text = describe(workout)
    if row.status == STATUS_PLANNED and row.scheduled_for:
        text += f"\n\n💾 *שמור ל{day_label(row.scheduled_for)}*"
    elif row.status == STATUS_SENT and row.scheduled_for:
        text += f"\n\n📲 *בשעון, ל{day_label(row.scheduled_for)}*"
    if row.status != STATUS_SENT and not can_send_to_watch(user, workout.sport):
        text += "\n\n_שליחה לשעון לענף הזה עוד בבדיקה. אפשר לשמור את האימון בבוט ולקבל עליו תזכורת._"
    return text


def _render(session: Session, user, workout_id: int):
    row, workout = load_workout(session, user.id, workout_id)
    ftp = user_ftp(session, user.id)
    return row, workout, ftp, preview_text(row, workout, user), workout_keyboard(row, workout, user, ftp)


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
                # A week from build_week is shown as one summary, not workout by workout.
                StoredWorkout.proposal_id.is_(None),
            )
            .order_by(col(StoredWorkout.id))
        ).all()
        previews = []
        for row in rows:
            try:
                _, _, _, text, keyboard = _render(session, user, row.id)
            except WorkoutNotFound:
                continue
            previews.append((text, keyboard))
    for text, keyboard in previews:
        await _reply(update, text, reply_markup=keyboard)


def retire_old_version(tasks, session: Session, user, row: StoredWorkout) -> None:
    """A new version was kept: the one it replaces goes - from the watch through the worker."""
    old_on_watch = take_over_from_old(session, user.id, row)
    if old_on_watch is not None:
        tasks.remove_workout_task.delay(user.id, old_on_watch, True)


async def _edit(query, text: str, keyboard) -> None:
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest:
        await query.edit_message_text(text, reply_markup=keyboard)


# --- the buttons ---------------------------------------------------------------------

async def on_workout_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Imported here: the bot must not drag Celery's task registry in at
    # module load just to register handlers.
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
    day = decode_day(parts[3]) if len(parts) == 4 else None

    with Session(engine) as session:
        user = _find_user(session, update.effective_chat.id)
        try:
            row, workout, ftp, text, keyboard = _render(session, user, workout_id)
        except WorkoutNotFound:
            await query.message.reply_text("האימון הזה כבר לא קיים.")
            return
        allowed = can_send_to_watch(user, workout.sport)

        if action == "day":
            await query.edit_message_reply_markup(reply_markup=picker_keyboard(row))
            return

        if action == "del":
            await query.edit_message_reply_markup(reply_markup=delete_confirm_keyboard(row))
            return

        if action == "edit":
            context.user_data[EDIT_KEY] = workout_id
            context.user_data[CHAT_MODE_KEY] = True
            await query.message.reply_text(
                f"✏️ מה לשנות ב*{row.name}*? למשל: _תקצר ל-40 דקות_ או _תחליף את האינטרוולים בריצה קלה_",
                parse_mode="Markdown",
            )
            return

        if action == "pick" and day is not None:
            await query.edit_message_reply_markup(reply_markup=confirm_keyboard(row, workout, user, day))
            return

        if action in ("back", "open"):
            if action == "open":
                await _reply(update, text, reply_markup=keyboard)
            else:
                await query.edit_message_reply_markup(reply_markup=keyboard)
            return

        if action == "save" and day is not None:
            plan_workout(session, user.id, workout_id, day)
            retire_old_version(tasks, session, user, row)
            _, _, _, text, keyboard = _render(session, user, workout_id)
            await _edit(query, text, keyboard)
            return

        if action == "send" and day is not None:
            if not allowed:
                await query.message.reply_text("שליחה לשעון לענף הזה עוד לא פתוחה.")
                return
            if row.status != STATUS_SENT:
                # Saved for that day first: if Garmin refuses or throttles,
                # the workout is still in the athlete's week and reminder.
                plan_workout(session, user.id, workout_id, day)
            retire_old_version(tasks, session, user, row)
            await query.edit_message_reply_markup(reply_markup=None)
            tasks.send_workout_task.delay(user.id, workout_id, day.isoformat())
            await query.message.reply_text(f"⏳ שולח לשעון ל{day_label(day)}...")
            return

        if action == "drop":
            await query.edit_message_reply_markup(reply_markup=None)
            if row.status == STATUS_SENT:
                tasks.remove_workout_task.delay(user.id, workout_id)
                await query.message.reply_text("⏳ מסיר מהלוח של Garmin...")
            else:
                discard_draft(session, user.id, workout_id)
                await query.message.reply_text("🗑 נמחק.")
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


# --- the builder -------------------------------------------------------------------
# sport -> by time or distance -> how much -> focus -> the coach builds it.
# The day travels along in every callback ("0" = none), so a workout started
# from a day in the week view arrives already set for that day.

BUILDER_KEY = "workout_builder"
# Set by "✏️ שינוי עם המאמן": the next typed message is a change to that workout.
EDIT_KEY = "workout_edit"
# Set by "✍️ אכתוב בעצמי" from a day in the week: the next typed request to
# the coach is for that day.
FREE_DAY_KEY = "workout_builder_day"
BUILDER_SPORTS = ["running", "cycling", "swimming", "strength", "walking", "hiit", "yoga", "mobility"]
DURATIONS = [20, 30, 45, 60, 90]
# Sports a workout can be built by distance for. The athlete types the
# distance itself ("5.5") - any number, not a list of presets. It travels
# in metres, so 5.5 km is 5500 and callback data never needs a decimal point.
DISTANCE_SPORTS = {"running", "walking", "swimming", "cycling"}
# What a typed amount may be. Outside these it is a typo, not a workout.
TIME_RANGE_MINUTES = (5, 300)
DISTANCE_RANGE_METRES = {
    "running": (1000, 50000),
    "walking": (1000, 40000),
    "swimming": (100, 10000),
    "cycling": (5000, 300000),
}
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


def amount_text(sport: str, mode: str, amount: int) -> str:
    if mode == "t":
        return f"{amount} דק'"
    if sport == "swimming":
        return f"{amount:,} מ'"
    return f"{amount / 1000:g} ק\"מ"


def sport_keyboard(day_code: str = "0") -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"{SPORT_LABELS[s][0]} {SPORT_LABELS[s][1]}", callback_data=f"wkb:s:{s}:{day_code}")
        for s in BUILDER_SPORTS
    ]
    return InlineKeyboardMarkup(_rows(buttons, 2))


def _back(callback: str) -> InlineKeyboardButton:
    return InlineKeyboardButton("↩️ חזרה", callback_data=callback)


def back_from_amount(sport: str, day_code: str) -> str:
    """Where ↩️ goes from choosing an amount: the time/distance question, or the sport list."""
    return f"wkb:s:{sport}:{day_code}" if sport in DISTANCE_SPORTS else f"wkb:home:{day_code}"


def mode_keyboard(sport: str, day_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏱ לפי זמן", callback_data=f"wkb:m:{sport}:t:{day_code}"),
            InlineKeyboardButton("📏 לפי מרחק", callback_data=f"wkb:m:{sport}:d:{day_code}"),
        ],
        [_back(f"wkb:home:{day_code}")],
    ])


def amount_keyboard(sport: str, day_code: str) -> InlineKeyboardMarkup:
    """Common lengths, and "✍️ זמן אחר" for anything else. (Distance is always typed.)"""
    buttons = [
        InlineKeyboardButton(amount_text(sport, "t", v), callback_data=f"wkb:a:{sport}:t:{v}:{day_code}")
        for v in DURATIONS
    ]
    rows = _rows(buttons, 3)
    rows.append([InlineKeyboardButton("✍️ זמן אחר", callback_data=f"wkb:o:{sport}:t:{day_code}")])
    rows.append([_back(back_from_amount(sport, day_code))])
    return InlineKeyboardMarkup(rows)


def typing_keyboard(sport: str, mode: str, day_code: str) -> InlineKeyboardMarkup:
    """Under "type an amount": ↩️ to the buttons it came from."""
    target = f"wkb:m:{sport}:t:{day_code}" if mode == "t" else back_from_amount(sport, day_code)
    return InlineKeyboardMarkup([[_back(target)]])


def distance_question(sport: str) -> str:
    if sport == "swimming":
        return "📏 כמה מטרים? כתוב מספר, למשל *1500*"
    return "📏 כמה ק\"מ? כתוב מספר, למשל *5.5*"


def focus_keyboard(sport: str, mode: str, amount: int, day_code: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(label, callback_data=f"wkb:f:{sport}:{mode}:{amount}:{key}:{day_code}")
        for key, label in FOCUS[sport].items()
    ]
    rows = _rows(buttons, 2)
    rows.append([InlineKeyboardButton("✍️ אכתוב בעצמי", callback_data=f"wkb:free:{day_code}")])
    rows.append([_back(f"wkb:m:{sport}:{mode}:{day_code}")])
    return InlineKeyboardMarkup(rows)


def _title(sport: str, day: Optional[date]) -> str:
    emoji, label = SPORT_LABELS[sport]
    return f"{emoji} {label}" + (f" · {day_label(day)}" if day else "")


def builder_request(sport: str, mode: str, amount: int, focus: str, day: Optional[date] = None) -> str:
    """What the builder asks the coach - plain words, as if the athlete typed it."""
    label = SPORT_LABELS[sport][1]
    if mode == "t":
        size = f"של כ-{amount} דקות"
    elif sport == "swimming":
        size = f"של {amount:,} מטר בסך הכל"
    else:
        size = f"של {amount / 1000:g} ק\"מ בסך הכל"
    text = f"תבנה לי אימון {label} {size}, מיקוד: {FOCUS[sport][focus]}"
    if day:
        text += f", ליום {DAY_NAMES_HE[week_index(day)]} {day.strftime('%d.%m')}"
    text += ". תתאים אותו לנתונים ולתוכנית שלי, ותשמור אותו עם build_workout"
    if day:
        text += f" עם date={day.isoformat()}"
    return text + "."


def _valid_amount(sport: str, mode: str, amount: int) -> bool:
    if mode == "t":
        return TIME_RANGE_MINUTES[0] <= amount <= TIME_RANGE_MINUTES[1]
    low, high = DISTANCE_RANGE_METRES.get(sport, (0, -1))
    return low <= amount <= high


async def on_builder_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.bot.coach import run_coach_turn

    query = update.callback_query
    await query.answer()
    if await _require_active(update) is None:
        return
    parts = (query.data or "").split(":")
    step = parts[1] if len(parts) > 1 else ""
    # Any step - forward or ↩️ - leaves a half-typed amount behind.
    context.user_data.pop(BUILDER_KEY, None)

    if step == "home" and len(parts) == 3:
        day = decode_day(parts[2])
        title = f"🏋️ אימון ל{day_label(day)} - איזה?" if day else "🏋️ *בניית אימון*\n\nאיזה אימון?"
        await query.edit_message_text(title, parse_mode="Markdown", reply_markup=sport_keyboard(parts[2]))
        return

    if step == "day" and len(parts) == 3:
        day = decode_day(parts[2])
        if day is None:
            return
        await query.message.reply_text(
            f"🏋️ אימון ל{day_label(day)} - איזה?", reply_markup=sport_keyboard(encode_day(day))
        )
        return

    if step == "s" and len(parts) in (3, 4) and parts[2] in FOCUS:
        sport, day_code = parts[2], parts[3] if len(parts) == 4 else "0"
        if sport in DISTANCE_SPORTS:
            await query.edit_message_text(
                f"{_title(sport, decode_day(day_code))}\n\nלפי זמן או לפי מרחק?", reply_markup=mode_keyboard(sport, day_code)
            )
        else:
            await query.edit_message_text(
                f"{_title(sport, decode_day(day_code))}\n\nכמה זמן?", reply_markup=amount_keyboard(sport, day_code)
            )
        return

    if step == "m" and len(parts) == 5 and parts[2] in FOCUS and parts[3] in ("t", "d"):
        sport, mode, day_code = parts[2], parts[3], parts[4]
        if mode == "t":
            await query.edit_message_text(
                f"{_title(sport, decode_day(day_code))}\n\nכמה זמן?", reply_markup=amount_keyboard(sport, day_code)
            )
            return
        if sport not in DISTANCE_SPORTS:
            return
        # Straight to typing: the distance is whatever the athlete wants.
        context.user_data[BUILDER_KEY] = {"sport": sport, "mode": "d", "day": day_code}
        await query.edit_message_text(
            f"{_title(sport, decode_day(day_code))}\n\n{distance_question(sport)}",
            parse_mode="Markdown",
            reply_markup=typing_keyboard(sport, "d", day_code),
        )
        return

    if step == "a" and len(parts) == 6 and parts[2] in FOCUS and parts[3] in ("t", "d") and parts[4].isdigit():
        sport, mode, amount, day_code = parts[2], parts[3], int(parts[4]), parts[5]
        if not _valid_amount(sport, mode, amount):
            return
        await query.edit_message_text(
            f"{_title(sport, decode_day(day_code))} · {amount_text(sport, mode, amount)}\n\nמה המיקוד?",
            reply_markup=focus_keyboard(sport, mode, amount, day_code),
        )
        return

    if step == "o" and len(parts) == 5 and parts[2] in FOCUS and parts[3] in ("t", "d"):
        sport, mode, day_code = parts[2], parts[3], parts[4]
        context.user_data[BUILDER_KEY] = {"sport": sport, "mode": mode, "day": day_code}
        if mode == "t":
            ask = "⏱ כמה זמן? כתוב דקות, למשל *75*, או שעות ודקות, למשל *1:15*"
        else:
            ask = distance_question(sport)
        await query.edit_message_text(
            f"{_title(sport, decode_day(day_code))}\n\n{ask}",
            parse_mode="Markdown",
            reply_markup=typing_keyboard(sport, mode, day_code),
        )
        return

    if step == "f" and len(parts) == 7 and parts[2] in FOCUS and parts[3] in ("t", "d") and parts[4].isdigit():
        sport, mode, amount, focus, day_code = parts[2], parts[3], int(parts[4]), parts[5], parts[6]
        if focus not in FOCUS[sport] or not _valid_amount(sport, mode, amount):
            return
        day = decode_day(day_code)
        await query.edit_message_text(
            f"🛠 בונה: {_title(sport, day)} · {amount_text(sport, mode, amount)} · {FOCUS[sport][focus]}..."
        )
        context.user_data[CHAT_MODE_KEY] = True  # "make it shorter" goes straight to the coach
        await run_coach_turn(update, context, builder_request(sport, mode, amount, focus, day))
        return

    if step == "free":
        day = decode_day(parts[2]) if len(parts) == 3 else None
        context.user_data[CHAT_MODE_KEY] = True
        when = f" ל{day_label(day)}" if day else ""
        if day:
            context.user_data[FREE_DAY_KEY] = day.isoformat()
        await query.edit_message_text(
            f"✍️ תכתוב איזה אימון אתה רוצה{when}, למשל: _6×800 בקצב 10 ק\"מ עם 2 דקות מנוחה_",
            parse_mode="Markdown",
        )


async def handle_builder_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """A typed time or distance for the builder ('✍️ זמן אחר' / 'מרחק אחר')."""
    state = context.user_data.get(BUILDER_KEY) or {}
    sport, mode, day_code = state.get("sport"), state.get("mode"), state.get("day", "0")
    if sport not in FOCUS or mode not in ("t", "d"):
        context.user_data.pop(BUILDER_KEY, None)
        return

    if mode == "t":
        amount = parse_duration(text)
        example = "למשל 75, או 1:15"
    else:
        metres = parse_distance(text, is_swim=sport == "swimming")
        amount = int(round(metres)) if metres else None
        example = "למשל 1500" if sport == "swimming" else "למשל 5.5"

    if amount is None or not _valid_amount(sport, mode, amount):
        if mode == "t":
            low, high = TIME_RANGE_MINUTES
            bounds = f"בין {low} ל-{high} דקות"
        else:
            low, high = DISTANCE_RANGE_METRES[sport]
            bounds = f"בין {amount_text(sport, 'd', low)} ל-{amount_text(sport, 'd', high)}"
        await update.message.reply_text(f"לא הצלחתי להבין. כתוב מספר {bounds}, {example}.")
        return

    context.user_data.pop(BUILDER_KEY, None)
    await update.message.reply_text(
        f"{_title(sport, decode_day(day_code))} · {amount_text(sport, mode, amount)}\n\nמה המיקוד?",
        reply_markup=focus_keyboard(sport, mode, amount, day_code),
    )


def with_free_day(context: ContextTypes.DEFAULT_TYPE, text: str) -> str:
    """
    The athlete's own words, plus what a button just set up (once): the day
    picked in the week view, or the workout they asked to change.
    """
    notes = []
    iso = context.user_data.pop(FREE_DAY_KEY, None)
    if iso:
        notes.append(f"האימון הזה ליום {iso} - שמור אותו עם build_workout עם date={iso}.")
    editing = context.user_data.pop(EDIT_KEY, None)
    if editing:
        notes.append(
            f"המתאמן רוצה לשנות את האימון workout_id={editing}. בנה גרסה חדשה עם build_workout "
            f"ו-replaces_workout_id={editing} (את הישן תמצא ב-get_training_schedule)."
        )
    return text + "".join(f"\n\n({note})" for note in notes)
