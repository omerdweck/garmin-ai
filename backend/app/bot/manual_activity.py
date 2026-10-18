"""
Adding a workout by hand, step by step.

Moved verbatim from the former single-file app/bot.py.
"""
import logging

from sqlmodel import Session
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    ApplicationHandlerStop,
    ContextTypes,
)

from app.db.session import engine
from app.services.manual_activity import (
    DISTANCE_CHOICES_KM,
    DURATION_CHOICES,
    MANUAL_TYPES,
    SWIM_CHOICES_M,
    build_start_time,
    create_manual_activity,
    parse_distance,
    parse_duration,
    takes_distance,
)
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.manual_activity import type_label as manual_type_label
from app.bot.calories import CALORIE_KEY, _handle_calorie_text
from app.bot.common import MAIN_KEYBOARD, MENU_BUTTONS, _find_user, _require_active

logger = logging.getLogger(__name__)



MANUAL_KEY = "manual_activity"


async def add_activity_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Opens manual entry, leading with when NOT to use it.

    The warning is the first thing shown rather than a footnote: a workout
    the watch already recorded will arrive from Garmin on the next sync, and
    the result is the same session stored twice. Nothing merges them, so the
    cheapest place to prevent that is before the first tap.
    """
    user_id = await _require_active(update)
    if user_id is None:
        return

    context.user_data[MANUAL_KEY] = {}
    await update.message.reply_text(
        "➕ *הוספת אימון ידנית*\n\n"
        "⚠️ השתמש בזה *רק לאימון שהשעון לא הקליט* - בלי שעון, או שכחת להתחיל הקלטה.\n\n"
        "אימון שהשעון כן מדד יגיע אלינו לבד בסנכרון הבא, והוספה ידנית שלו "
        "תיצור אותו אימון פעמיים.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.effective_chat.send_message(
        "איזה סוג אימון?",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(label, callback_data=f"manual:type:{key}")]
                for key, label in MANUAL_TYPES
            ]
            + [[InlineKeyboardButton("ביטול", callback_data="manual:abort")]]
        ),
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
            create_manual_activity(
                session,
                user_id=user.id,
                activity_type=draft["type"],
                start_time=build_start_time(draft.get("day_offset", 0)),
                duration_minutes=draft["duration"],
                distance_meters=draft.get("distance_m"),
            )
            summary = _manual_summary(draft)

        context.user_data.pop(MANUAL_KEY, None)
        await query.edit_message_text(f"✅ *נשמר*\n\n{summary}", parse_mode="Markdown")
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

    if not (draft and draft.get("awaiting")) and not (
        calorie_draft and calorie_draft.get("awaiting")
    ):
        return

    text = update.message.text

    # Checked before either branch: a menu tap is a text message too, and
    # swallowing it is what trapped users before. Whichever flow is waiting,
    # pressing a button is a clear "I am done here".
    if text in MENU_BUTTONS:
        context.user_data.pop(MANUAL_KEY, None)
        context.user_data.pop(CALORIE_KEY, None)
        logger.info("Pending entry abandoned via menu button by chat %s", update.effective_chat.id)
        return

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
