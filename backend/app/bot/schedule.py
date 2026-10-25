"""
"🗓 לו״ז אימונים שבועי" - the week from Sunday to Saturday - and the review
of a week the coach proposed with build_week.

The schedule shows what actually happened as well as what was planned, so a
Tuesday visit shows Sunday's and Monday's workouts whether they were planned
or not. ◀️ ▶️ move between weeks; ➕ adds a workout to any day from today on.
"""

from datetime import datetime

from sqlmodel import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.bot.common import _find_user, _reply, _require_active
from app.bot.workouts import (
    retire_old_version,
    encode_day,
)
from app.core import clock
from app.db.session import engine
from app.models.plan_session import DAY_NAMES_HE, week_index
from app.models.workout import STATUS_DRAFT, STATUS_PLANNED, STATUS_REMOVED, STATUS_SENT
from app.services.activity_view import type_label
from app.services.plan_view import active_plans, day_label, discipline_label
from app.services.schedule import DONE, MISSED, activity_family, week_schedule
from app.services.workout_schema import estimated_seconds, parse_workout
from app.services.workout_view import SPORT_LABELS, describe
from app.services.workouts import (
    can_send_to_watch,
    discard_draft,
    plan_workout,
    proposal_rows,
    latest_proposal_since,
)

OLDEST_WEEK, NEWEST_WEEK = -4, 2


# --- the weekly schedule ----------------------------------------------------------

def _activity_line(activity) -> str:
    parts = [type_label(activity.activity_type)]
    if activity.distance_meters and activity.distance_meters > 100:
        parts.append(f"{activity.distance_meters / 1000:.1f} ק\"מ")
    if activity.duration_seconds:
        parts.append(f"{round(activity.duration_seconds / 60)} דק'")
    return " · ".join(parts)


def _planned_line(workout, state: str) -> str:
    emoji = SPORT_LABELS.get(workout.sport, ("🏅", ""))[0]
    if state == DONE:
        return f"✅ {emoji} *{workout.name}* · בוצע"
    if state == MISSED:
        return f"⏭ {emoji} {workout.name} · לא בוצע"
    where = "📲 בשעון" if workout.status == STATUS_SENT else "💾 בבוט"
    return f"🗓 {emoji} *{workout.name}* · {where}"


def format_schedule(week) -> str:
    today = clock.today()
    first, last = week[0].day, week[-1].day
    lines = [f"🗓 *לו״ז אימונים · {first.strftime('%d.%m')}–{last.strftime('%d.%m')}*", ""]
    done_count = planned_count = 0

    for day in week:
        marker = " _(היום)_" if day.day == today else ""
        lines.append(f"*{DAY_NAMES_HE[week_index(day.day)]} {day.day.strftime('%d.%m')}*{marker}")
        items = []

        # A planned workout that was done is shown once, as done - the
        # activity that fulfilled it is not listed again beside it.
        unmatched = list(day.done)
        for workout, state in day.planned:
            if state == DONE:
                match = next((a for a in unmatched if activity_family(a.activity_type) == workout.sport), None)
                if match is not None:
                    unmatched.remove(match)
                done_count += 1
            elif state != MISSED:
                planned_count += 1
            items.append(_planned_line(workout, state))
        for activity in unmatched:
            items.append(f"✅ {_activity_line(activity)}")
            done_count += 1
        for item, discipline in day.template:
            label = discipline_label(discipline).split(" ", 1)[0]
            items.append(f"📋 {label} {item.title}" + (" 😴" if item.is_rest else ""))

        lines += [f"   {item}" for item in items] or ["   —"]
        lines.append("")

    lines.append(f"✅ בוצעו *{done_count}* · 🗓 מתוכננים *{planned_count}*")
    return "\n".join(lines)


def schedule_keyboard(week, offset: int, has_template: bool) -> InlineKeyboardMarkup:
    today = clock.today()
    rows = []

    nav = []
    if offset > OLDEST_WEEK:
        nav.append(InlineKeyboardButton("◀️ שבוע קודם", callback_data=f"sch:{offset - 1}"))
    if offset != 0:
        nav.append(InlineKeyboardButton("השבוע", callback_data="sch:0"))
    if offset < NEWEST_WEEK:
        nav.append(InlineKeyboardButton("שבוע הבא ▶️", callback_data=f"sch:{offset + 1}"))
    rows.append(nav)

    add = [
        InlineKeyboardButton(f"➕ {day_label(d.day, today)}", callback_data=f"wkb:day:{encode_day(d.day)}")
        for d in week
        if d.day >= today
    ]
    rows += [add[i : i + 4] for i in range(0, len(add), 4)]

    for day in week:
        for workout, state in day.planned:
            if state == DONE:
                continue
            rows.append([InlineKeyboardButton(
                f"🔍 {workout.name} · {day_label(day.day, today)}", callback_data=f"wk:open:{workout.id}"
            )])

    rows.append([InlineKeyboardButton("🗓 בניית לו״ז עם המאמן", callback_data="plan:new")])
    if has_template:
        rows.append([InlineKeyboardButton("🗑 מחיקת התבנית הישנה", callback_data="plan:delete")])
    return InlineKeyboardMarkup(rows)


def _schedule_view(user_id: int, offset: int):
    with Session(engine) as session:
        week = week_schedule(session, user_id, offset)
        has_template = bool(active_plans(session, user_id))
        return format_schedule(week), schedule_keyboard(week, offset, has_template)


async def show_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = await _require_active(update)
    if user_id is None:
        return
    text, keyboard = _schedule_view(user_id, 0)
    await _reply(update, text, reply_markup=keyboard)


async def on_schedule_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = await _require_active(update)
    if user_id is None:
        return
    try:
        offset = int((query.data or "").split(":")[1])
    except (IndexError, ValueError):
        return
    if not OLDEST_WEEK <= offset <= NEWEST_WEEK:
        return
    text, keyboard = _schedule_view(user_id, offset)
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest:
        await query.edit_message_text(text, reply_markup=keyboard)


# --- a week the coach proposed -------------------------------------------------------

def proposal_summary(rows) -> str:
    lines = [f"🗓 *הצעה לשבוע · {len(rows)} אימונים*", ""]
    for row in rows:
        emoji = SPORT_LABELS.get(row.sport, ("🏅", ""))[0]
        try:
            minutes = round(estimated_seconds(_parse(row)) / 60)
            length = f" · כ-{minutes} דק'"
        except ValueError:
            length = ""
        lines.append(f"*{DAY_NAMES_HE[week_index(row.scheduled_for)]} {row.scheduled_for.strftime('%d.%m')}* — {emoji} {row.name}{length}")
    lines.append("")
    lines.append("_אפשר לעבור על כל אימון, לשלוח הכל, או לבקש מהמאמן שינויים._")
    return "\n".join(lines)


def _parse(row):
    workout, errors = parse_workout(row.spec)
    if workout is None:
        raise ValueError(errors)
    return workout


def proposal_keyboard(proposal_id: str, rows, user) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("▶️ לעבור על האימונים", callback_data=f"wkw:r:{proposal_id}:0")]]
    bulk = []
    if any(can_send_to_watch(user, r.sport) for r in rows):
        bulk.append(InlineKeyboardButton("📲 הכל לשעון", callback_data=f"wkw:all:{proposal_id}"))
    bulk.append(InlineKeyboardButton("💾 הכל בבוט", callback_data=f"wkw:keep:{proposal_id}"))
    buttons.append(bulk)
    buttons.append([InlineKeyboardButton("🗑 לא מתאים", callback_data=f"wkw:drop:{proposal_id}")])
    return InlineKeyboardMarkup(buttons)


async def show_week_proposal(update: Update, chat_id: int, since: datetime) -> None:
    """After a coach turn that ran build_week: the proposed week and its buttons."""
    with Session(engine) as session:
        user = _find_user(session, chat_id)
        if user is None:
            return
        proposal_id = latest_proposal_since(session, user.id, since)
        if proposal_id is None:
            return
        rows = proposal_rows(session, user.id, proposal_id)
        text, keyboard = proposal_summary(rows), proposal_keyboard(proposal_id, rows, user)
    await _reply(update, text, reply_markup=keyboard)


STATE_LABELS = {STATUS_PLANNED: "💾 נשמר בבוט", STATUS_SENT: "📲 בשעון", STATUS_REMOVED: "⏭ דילגת"}
SENDING_KEY = "week_review_sending"


def review_view(rows, index: int, user, sending: set) -> tuple[str, InlineKeyboardMarkup]:
    row = rows[index]
    workout = _parse(row)
    head = f"*אימון {index + 1} מתוך {len(rows)}* · {day_label(row.scheduled_for)}"
    text = f"{head}\n\n{describe(workout)}"
    proposal_id = row.proposal_id

    buttons = []
    if row.status == STATUS_DRAFT:
        actions = []
        if can_send_to_watch(user, row.sport):
            actions.append(InlineKeyboardButton("📲 לשעון", callback_data=f"wkw:sw:{proposal_id}:{index}"))
        actions.append(InlineKeyboardButton("💾 רק בבוט", callback_data=f"wkw:kp:{proposal_id}:{index}"))
        actions.append(InlineKeyboardButton("⏭ לא צריך", callback_data=f"wkw:sk:{proposal_id}:{index}"))
        buttons.append(actions)
    else:
        label = "📲 בדרך לשעון" if row.id in sending and row.status != STATUS_SENT else STATE_LABELS.get(row.status, "")
        text += f"\n\n*{label}*"

    nav = []
    if index > 0:
        nav.append(InlineKeyboardButton("◀️ הקודם", callback_data=f"wkw:r:{proposal_id}:{index - 1}"))
    if index < len(rows) - 1:
        nav.append(InlineKeyboardButton("הבא ▶️", callback_data=f"wkw:r:{proposal_id}:{index + 1}"))
    else:
        nav.append(InlineKeyboardButton("✋ סיום", callback_data=f"wkw:end:{proposal_id}"))
    buttons.append(nav)
    return text, InlineKeyboardMarkup(buttons)


def review_summary(rows, sending: set) -> str:
    to_watch = sum(1 for r in rows if r.status == STATUS_SENT or (r.id in sending and r.status != STATUS_REMOVED))
    kept = sum(1 for r in rows if r.status == STATUS_PLANNED and r.id not in sending)
    skipped = sum(1 for r in rows if r.status == STATUS_REMOVED)
    waiting = sum(1 for r in rows if r.status == STATUS_DRAFT)
    lines = ["✅ *סיימנו*", ""]
    if to_watch:
        lines.append(f"📲 {to_watch} לשעון")
    if kept:
        lines.append(f"💾 {kept} בבוט")
    if skipped:
        lines.append(f"⏭ {skipped} לא נכנסו")
    if waiting:
        lines.append(f"❔ {waiting} עוד לא החלטת - אפשר לחזור אליהם")
    lines += ["", "הכל מופיע ב🗓 לו״ז אימונים שבועי."]
    return "\n".join(lines)


async def _edit(query, text, keyboard=None) -> None:
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except BadRequest:
        await query.edit_message_text(text, reply_markup=keyboard)


def _keep(tasks, session, user, row, to_watch: bool, sending: set, delay: int = 0) -> None:
    """Save one proposed workout for its day, and send it on if asked and allowed."""
    plan_workout(session, user.id, row.id, row.scheduled_for)
    retire_old_version(tasks, session, user, row)
    if to_watch and can_send_to_watch(user, row.sport):
        tasks.send_workout_task.apply_async(args=[user.id, row.id, row.scheduled_for.isoformat()], countdown=delay)
        sending.add(row.id)


async def on_week_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app import tasks

    query = update.callback_query
    await query.answer()
    if await _require_active(update) is None:
        return
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    action, proposal_id = parts[1], parts[2]
    index = int(parts[3]) if len(parts) == 4 and parts[3].isdigit() else None
    sending: set = context.user_data.setdefault(SENDING_KEY, set())

    with Session(engine) as session:
        user = _find_user(session, update.effective_chat.id)
        # Including removed ones, so positions stay put while skipping.
        rows = proposal_rows(session, user.id, proposal_id, include_removed=True)
        if not rows:
            await query.message.reply_text("ההצעה הזו כבר לא קיימת. אפשר לבקש מהמאמן שבוע חדש.")
            return

        if action in ("sw", "kp", "sk") and index is not None and index < len(rows):
            row = rows[index]
            if row.status == STATUS_DRAFT:
                if action == "sk":
                    discard_draft(session, user.id, row.id)
                else:
                    _keep(tasks, session, user, row, to_watch=action == "sw", sending=sending)
            rows = proposal_rows(session, user.id, proposal_id, include_removed=True)
            index = index + 1
            if index >= len(rows):
                await _edit(query, review_summary(rows, sending))
                return
            action = "r"

        if action == "r" and index is not None and index < len(rows):
            text, keyboard = review_view(rows, index, user, sending)
            await _edit(query, text, keyboard)
            return

        if action == "end":
            await _edit(query, review_summary(rows, sending))
            return

        if action in ("all", "keep"):
            for position, row in enumerate(r for r in rows if r.status == STATUS_DRAFT):
                # Spread out like the scheduled sync: one account, one IP.
                _keep(tasks, session, user, row, to_watch=action == "all", sending=sending, delay=position * 3)
            await _edit(query, review_summary(proposal_rows(session, user.id, proposal_id, include_removed=True), sending))
            return

        if action == "drop":
            for row in rows:
                if row.status == STATUS_DRAFT:
                    discard_draft(session, user.id, row.id)
            await _edit(query, "🗑 ההצעה בוטלה. אפשר לבקש מהמאמן שבוע אחר.")

