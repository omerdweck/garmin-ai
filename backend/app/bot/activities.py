"""
Browsing workouts by type, down to one workout's details, and personal records.

Moved verbatim from the former single-file app/bot.py.
"""
import asyncio

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
from app.models.activity import Activity
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.records import format_records
from app.services.activity_view import (
    activity_type_counts,
    format_activity_detail,
    recent_of_type,
    summary_line,
    type_label,
)
from app.services.garmin_sync import ensure_exercise_sets
from app.bot.common import MAIN_KEYBOARD, _find_user, _require_active



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
    # Records live inside this screen rather than as an eleventh keyboard
    # button: they are a fact *about* these workouts, and the main keyboard
    # was deliberately cut to ten.
    rows.append([InlineKeyboardButton("🏆 שיאים אישיים", callback_data="records")])
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
    rows.append([InlineKeyboardButton("🏆 שיאים אישיים", callback_data="records")])
    await query.edit_message_text(
        "🏃 בחר סוג אימון 👇", reply_markup=InlineKeyboardMarkup(rows)
    )


async def on_records(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The stored personal records, with a way back to the workouts list."""
    query = update.callback_query
    await query.answer()

    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        if user is None:
            await query.edit_message_text("שלח /start כדי להתחיל 👋")
            return
        text = format_records(session, user.id)

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ חזרה לאימונים", callback_data="acttype_menu")]]
        ),
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
