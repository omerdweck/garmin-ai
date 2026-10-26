"""
"➕ הוספת אימון": first a choice between a workout already done and one to
do later, then either the manual entry below or the workout builder.

Also the delete of a manually entered workout - from the message that saved
it, and from the workout's page under "🏃 האימונים שלי".
"""
import logging
from datetime import timedelta

from sqlmodel import Session
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationHandlerStop,
    ContextTypes,
)

from app.core import clock
from app.core.config import settings
from app.db.session import engine
from app.models.activity import SOURCE_MANUAL, Activity
from app.services.activity_view import summary_line
from app.services.manual_activity import (
    DISTANCE_CHOICES_KM,
    DURATION_CHOICES,
    MANUAL_TYPES,
    SWIM_CHOICES_M,
    build_start_time,
    create_manual_activity,
    delete_manual_activity,
    parse_distance,
    parse_duration,
    takes_distance,
)
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.manual_activity import type_label as manual_type_label
from app.bot.calories import CALORIE_KEY, _handle_calorie_text
from app.bot.common import MAIN_KEYBOARD, MENU_BUTTONS, _find_user, _require_active
from app.bot.workouts import BUILDER_KEY, encode_day, handle_builder_text
from app.services.plan_view import day_label

logger = logging.getLogger(__name__)



MANUAL_KEY = "manual_activity"


# How far ahead "📅 to do later" offers days directly. The weekly schedule
# goes further; a week covers "later today" and "on Thursday".
FUTURE_DAYS = 7


async def add_activity_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Asks first what kind of "add": a workout already done, or one to do later.

    Both used to be one path, so a workout planned for this evening was
    stored as done - in the history, the weekly totals and the load - instead
    of becoming something to put on the watch.
    """
    user_id = await _require_active(update)
    if user_id is None:
        return

    context.user_data.pop(MANUAL_KEY, None)
    await update.message.reply_text(
        "➕ *הוספת אימון*\n\nמה תרצה להוסיף?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ אימון שכבר עשיתי", callback_data="manual:past")],
                [InlineKeyboardButton("📅 אימון לעשות בהמשך", callback_data="manual:future")],
                [InlineKeyboardButton("ביטול", callback_data="manual:abort")],
            ]
        ),
    )


async def _start_past(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Manual entry, leading with when NOT to use it.

    The warning comes first rather than as a footnote: a workout the watch
    already recorded arrives from Garmin on the next sync, and entering it
    by hand too stores the same session twice. Nothing merges them.
    """
    context.user_data[MANUAL_KEY] = {}
    await query.edit_message_text(
        "✅ *אימון שכבר עשיתי*\n\n"
        "⚠️ השתמש בזה *רק לאימון שהשעון לא הקליט* - בלי שעון, או שכחת להתחיל הקלטה.\n\n"
        "אימון שהשעון כן מדד יגיע אלינו לבד בסנכרון הבא, והוספה ידנית שלו "
        "תיצור אותו אימון פעמיים.",
        parse_mode="Markdown",
    )
    await query.message.chat.send_message(
        "איזה סוג אימון?",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(label, callback_data=f"manual:type:{key}")]
                for key, label in MANUAL_TYPES
            ]
            + [[InlineKeyboardButton("ביטול", callback_data="manual:abort")]]
        ),
    )


async def _start_future(query) -> None:
    """
    A workout to do later goes to the workout builder, for a chosen day:
    the coach builds it, and from there it can go to the watch. The day
    buttons hand over to the builder's own "wkb:home:<day>" step, so this
    adds no second way of building workouts.
    """
    if not settings.is_claude_configured:
        await query.edit_message_text("🤖 המאמן החכם לא מחובר כרגע, ולכן אי אפשר לבנות אימון עתידי.")
        return
    today = clock.today()
    days = [today + timedelta(days=i) for i in range(FUTURE_DAYS)]
    buttons = [InlineKeyboardButton(day_label(d, today), callback_data=f"wkb:home:{encode_day(d)}") for d in days]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("ביטול", callback_data="manual:abort")])
    await query.edit_message_text(
        "📅 *אימון לעשות בהמשך*\n\n"
        "לאיזה יום? אחרי שהמאמן יבנה אותו, תוכל לשלוח אותו לשעון.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def delete_keyboard(activity_id: int) -> InlineKeyboardMarkup:
    """The 🗑 button under a manual workout. Opens a confirmation, never deletes on one tap."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🗑 מחיקת האימון", callback_data=f"manual:del:{activity_id}")]])


async def _on_delete(query, step: str, activity_id: int) -> None:
    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        activity = session.get(Activity, activity_id)
        # Callback data comes from the client: ownership and source are
        # checked here (and again inside delete_manual_activity).
        if user is None or activity is None or activity.user_id != user.id or activity.source != SOURCE_MANUAL:
            await query.edit_message_text("האימון לא נמצא 🤔 אולי כבר נמחק.")
            return
        label = f"{manual_type_label(activity.activity_type)} · {summary_line(activity)}"

        if step == "del":
            await query.edit_message_text(
                f"למחוק את האימון הזה?\n\n*{label}*\n\n_אי אפשר לבטל מחיקה._",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("🗑 כן, למחוק", callback_data=f"manual:delok:{activity_id}"),
                        InlineKeyboardButton("השאר", callback_data=f"manual:keep:{activity_id}"),
                    ]]
                ),
            )
            return

        if step == "keep":
            await query.edit_message_text(f"👍 האימון נשאר\n\n*{label}*", parse_mode="Markdown",
                                          reply_markup=delete_keyboard(activity_id))
            return

        delete_manual_activity(session, user.id, activity_id)
    logger.info("Manual activity %s deleted by chat %s", activity_id, query.message.chat_id)
    await query.edit_message_text(
        f"🗑 *נמחק*\n\n{label}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ לאימונים שלי", callback_data="acttype_menu")]]),
    )


async def on_manual_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Every step of manual entry. One handler rather than a ConversationHandler
    because the whole flow is inline buttons - there is no free text to
    capture, so there is no state a conversation would be tracking that the
    callback data does not already carry.
    """
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    step = parts[1]

    if step == "past":
        await _start_past(query, context)
        return
    if step == "future":
        context.user_data.pop(MANUAL_KEY, None)
        await _start_future(query)
        return
    if step in ("del", "delok", "keep") and len(parts) == 3 and parts[2].isdigit():
        await _on_delete(query, step, int(parts[2]))
        return

    draft = context.user_data.setdefault(MANUAL_KEY, {})

    if step == "abort":
        context.user_data.pop(MANUAL_KEY, None)
        await query.edit_message_text("בוטל 👍")
        await query.message.chat.send_message("חזרה לתפריט 👇", reply_markup=MAIN_KEYBOARD)
        return

    if step == "type":
        draft["type"] = parts[2]
        await query.edit_message_text(
            f"{manual_type_label(draft['type'])} - מתי?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("היום", callback_data="manual:day:0"),
                        InlineKeyboardButton("אתמול", callback_data="manual:day:1"),
                    ],
                    [
                        InlineKeyboardButton("לפני יומיים", callback_data="manual:day:2"),
                        InlineKeyboardButton("לפני 3 ימים", callback_data="manual:day:3"),
                    ],
                    [InlineKeyboardButton("ביטול", callback_data="manual:abort")],
                ]
            ),
        )
        return

    if step == "day":
        draft["day_offset"] = int(parts[2])
        buttons = [
            InlineKeyboardButton(f"{m} דק'", callback_data=f"manual:dur:{m}")
            for m in DURATION_CHOICES
        ]
        rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
        rows.append([InlineKeyboardButton("✏️ להקליד משך אחר", callback_data="manual:typedur")])
        rows.append([InlineKeyboardButton("ביטול", callback_data="manual:abort")])
        await query.edit_message_text("כמה זמן?", reply_markup=InlineKeyboardMarkup(rows))
        return

    if step == "typedur":
        draft["awaiting"] = "duration"
        await query.edit_message_text(DURATION_PROMPT, parse_mode="Markdown")
        return

    if step == "typedist":
        draft["awaiting"] = "distance"
        await query.edit_message_text(
            _distance_prompt(draft["type"] == "lap_swimming"), parse_mode="Markdown"
        )
        return

    if step == "dur":
        draft["duration"] = int(parts[2])
        if not takes_distance(draft["type"]):
            # Strength and "other" have no meaningful distance - skipping the
            # question beats offering one the user has to dismiss every time.
            await _manual_confirm(query, draft)
            return

        swim = draft["type"] == "lap_swimming"
        choices = SWIM_CHOICES_M if swim else DISTANCE_CHOICES_KM
        unit = "מ'" if swim else "ק\"מ"
        buttons = [
            InlineKeyboardButton(f"{c} {unit}", callback_data=f"manual:dist:{c}")
            for c in choices
        ]
        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("✏️ להקליד מרחק אחר", callback_data="manual:typedist")])
        rows.append([InlineKeyboardButton("לא יודע / דלג", callback_data="manual:dist:skip")])
        rows.append([InlineKeyboardButton("ביטול", callback_data="manual:abort")])
        await query.edit_message_text("מה המרחק?", reply_markup=InlineKeyboardMarkup(rows))
        return

    if step == "dist":
        if parts[2] != "skip":
            value = float(parts[2])
            # Swimming is entered in metres, everything else in kilometres.
            draft["distance_m"] = value if draft["type"] == "lap_swimming" else value * 1000
        await _manual_confirm(query, draft)
        return

    if step == "save":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            if user is None:
                await query.edit_message_text("לא נמצא משתמש.")
                return
            saved = create_manual_activity(
                session,
                user_id=user.id,
                activity_type=draft["type"],
                start_time=build_start_time(draft.get("day_offset", 0)),
                duration_minutes=draft["duration"],
                distance_meters=draft.get("distance_m"),
            )
            summary = _manual_summary(draft)
            saved_id = saved.id

        context.user_data.pop(MANUAL_KEY, None)
        # The delete button stays under the saved message: the most likely
        # moment to notice a mistake is right after saving it.
        await query.edit_message_text(
            f"✅ *נשמר*\n\n{summary}", parse_mode="Markdown", reply_markup=delete_keyboard(saved_id)
        )
        await query.message.chat.send_message(
            "האימון נוסף לרשימת האימונים שלך 💪\n\n"
            "_שים לב: מדדי העומס של גרמין לא כוללים אימונים שהוזנו ידנית._",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return


def _manual_summary(draft: dict) -> str:
    when = {0: "היום", 1: "אתמול", 2: "לפני יומיים"}.get(
        draft.get("day_offset", 0), f"לפני {draft.get('day_offset')} ימים"
    )
    parts = [manual_type_label(draft["type"]), when, f"{draft['duration']} דק'"]
    dist = draft.get("distance_m")
    if dist:
        parts.append(f"{dist:g} מ'" if draft["type"] == "lap_swimming" else f'{dist / 1000:g} ק"מ')
    return " · ".join(parts)


# One example per line, lettered. A single line of examples separated by
# dots read as one run-on string, and the unit - the part that actually
# matters - got lost in it.
DURATION_PROMPT = (
    "⏱ *כמה זמן נמשך האימון?*\n\n"
    "כתוב את מספר הדקות. לדוגמה:\n"
    "א. `45` (45 דקות)\n"
    "ב. `45 דקות`\n"
    "ג. `1:15` (שעה ורבע)"
)


def _distance_prompt(is_swim: bool) -> str:
    """
    Spells out both units and shows how to write a short distance.

    Without the last example someone types "800" meaning metres and stores
    an 800 km run. The parser reads whichever unit is written, so the fix is
    to make sure the user knows they can write one.
    """
    if is_swim:
        return (
            "📏 *מה המרחק?*\n\n"
            "בשחייה אפשר לכתוב במטרים או בק\"מ. לדוגמה:\n"
            "א. `750` (750 מטר)\n"
            "ב. `1200 מטר`\n"
            "ג. `1.5 ק\"מ` (1500 מטר)\n\n"
            "_בלי ציון יחידה - נספר כמטרים._"
        )
    return (
        "📏 *מה המרחק?*\n\n"
        "אפשר לכתוב בק\"מ או במטרים. לדוגמה:\n"
        "א. `8` (8 ק\"מ)\n"
        "ב. `8.5 ק\"מ`\n"
        "ג. `800 מטר` (או `0.8`)\n\n"
        "_בלי ציון יחידה - נספר כקילומטרים._"
    )


def _manual_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ שמור", callback_data="manual:save"),
                InlineKeyboardButton("ביטול", callback_data="manual:abort"),
            ]
        ]
    )


async def on_manual_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Catches a typed duration or distance during manual entry.

    Registered in an earlier handler group than the coach, because the coach
    handler matches every plain message - without this ordering, "37" would
    be sent to Claude as a question. When this is not waiting for input it
    returns quietly and the coach handles the message as usual; when it is,
    ApplicationHandlerStop keeps the same text from reaching the coach too.
    """
    draft = context.user_data.get(MANUAL_KEY)
    calorie_draft = context.user_data.get(CALORIE_KEY)
    builder = context.user_data.get(BUILDER_KEY)

    if not (draft and draft.get("awaiting")) and not (
        calorie_draft and calorie_draft.get("awaiting")
    ) and not builder:
        return

    text = update.message.text

    # Checked before either branch: a menu tap is a text message too, and
    # swallowing it is what trapped users before. Whichever flow is waiting,
    # pressing a button is a clear "I am done here".
    if text in MENU_BUTTONS:
        context.user_data.pop(MANUAL_KEY, None)
        context.user_data.pop(CALORIE_KEY, None)
        context.user_data.pop(BUILDER_KEY, None)
        logger.info("Pending entry abandoned via menu button by chat %s", update.effective_chat.id)
        return

    if builder:
        # A typed time or distance for the workout builder.
        await handle_builder_text(update, context, text)
        raise ApplicationHandlerStop

    if calorie_draft and calorie_draft.get("awaiting"):
        await _handle_calorie_text(update, context, calorie_draft, text)
        raise ApplicationHandlerStop

    field = draft["awaiting"]

    if field == "duration":
        minutes = parse_duration(text)
        if minutes is None:
            # The error repeats the format rather than just saying no - a
            # rejection that does not show what was expected leaves the user
            # guessing at the same wall twice.
            await update.message.reply_text(
                "לא הצלחתי לקרוא את זה 🤔\n\n" + DURATION_PROMPT, parse_mode="Markdown"
            )
            raise ApplicationHandlerStop
        draft["duration"] = minutes
        draft.pop("awaiting")

        if not takes_distance(draft["type"]):
            await update.message.reply_text(
                f"לשמור את האימון הזה?\n\n*{_manual_summary(draft)}*",
                parse_mode="Markdown",
                reply_markup=_manual_confirm_keyboard(),
            )
            raise ApplicationHandlerStop

        swim = draft["type"] == "lap_swimming"
        choices = SWIM_CHOICES_M if swim else DISTANCE_CHOICES_KM
        unit = "מ'" if swim else 'ק"מ'
        buttons = [
            InlineKeyboardButton(f"{c} {unit}", callback_data=f"manual:dist:{c}")
            for c in choices
        ]
        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("✏️ להקליד מרחק אחר", callback_data="manual:typedist")])
        rows.append([InlineKeyboardButton("לא יודע / דלג", callback_data="manual:dist:skip")])
        rows.append([InlineKeyboardButton("ביטול", callback_data="manual:abort")])
        await update.message.reply_text("מה המרחק?", reply_markup=InlineKeyboardMarkup(rows))
        raise ApplicationHandlerStop

    if field == "distance":
        meters = parse_distance(text, draft["type"] == "lap_swimming")
        if meters is None:
            await update.message.reply_text(
                "לא הצלחתי לקרוא את זה 🤔\n\n"
                + _distance_prompt(draft["type"] == "lap_swimming"),
                parse_mode="Markdown",
            )
            raise ApplicationHandlerStop
        draft["distance_m"] = meters
        draft.pop("awaiting")
        await update.message.reply_text(
            f"לשמור את האימון הזה?\n\n*{_manual_summary(draft)}*",
            parse_mode="Markdown",
            reply_markup=_manual_confirm_keyboard(),
        )
        raise ApplicationHandlerStop


async def _manual_confirm(query, draft: dict) -> None:
    await query.edit_message_text(
        f"לשמור את האימון הזה?\n\n*{_manual_summary(draft)}*",
        parse_mode="Markdown",
        reply_markup=_manual_confirm_keyboard(),
    )
