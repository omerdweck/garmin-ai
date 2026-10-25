"""
Showing, approving, cancelling and deleting training plans.

Moved verbatim from the former single-file app/bot.py.
"""

from sqlmodel import Session
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ContextTypes,
)

from datetime import timedelta

from app.bot.workouts import encode_day
from app.core import clock
from app.db.session import engine
from app.services.workouts import scheduled_between
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.plan_view import (
    active_plans,
    approve_plans,
    cancel_plan,
    discard_pending,
    discipline_label,
    day_label,
    format_upcoming_week,
)
from app.bot.common import CHAT_KEYBOARD, CHAT_MODE_KEY, MAIN_KEYBOARD, _find_user, _reply, _reply_plain, _require_active



def week_keyboard(saved, has_plan: bool) -> InlineKeyboardMarkup:
    """
    ➕ for every day of the coming week (opens the workout builder already
    set for that day), a button per saved workout, and the plan actions.
    """
    today = clock.today()
    days = [today + timedelta(days=offset) for offset in range(7)]
    add = [
        InlineKeyboardButton(f"➕ {day_label(day, today)}", callback_data=f"wkb:day:{encode_day(day)}")
        for day in days
    ]
    rows = [add[:4], add[4:]]
    for workout in saved[:8]:
        rows.append([InlineKeyboardButton(
            f"🔍 {workout.name} · {day_label(workout.scheduled_for, today)}", callback_data=f"wk:open:{workout.id}"
        )])
    rows.append([InlineKeyboardButton("✨ בניית תוכנית חדשה", callback_data="plan:new")])
    if has_plan:
        rows.append([InlineKeyboardButton("🗑 מחיקת תוכנית", callback_data="plan:delete")])
    return InlineKeyboardMarkup(rows)


async def show_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    The week and the plan in one place: the next seven days (the weekly
    plan plus every workout saved for a date), ➕ to add a workout to any
    of them, and the plan actions. The delete action lives here rather than
    on the main keyboard - a destructive button sitting next to ones people
    press daily is a mis-tap waiting to happen, and it belongs with the
    thing it destroys.
    """
    user_id = await _require_active(update)
    if user_id is None:
        return

    with Session(engine) as session:
        text = format_upcoming_week(session, user_id)
        has_plan = bool(active_plans(session, user_id))
        today = clock.today()
        saved = scheduled_between(session, user_id, today, today + timedelta(days=6))
        keyboard = week_keyboard(saved, has_plan)

    if not text:
        text = (
            "📅 *השבוע שלך ריק*\n\n"
            "אפשר להוסיף אימון לכל יום עם ➕, או לבנות תוכנית שבועית שלמה עם המאמן 👇"
        )
    await _reply(update, text, reply_markup=keyboard)


async def on_plan_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The plan hub's buttons."""
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]

    if action == "new":
        # Entering chat mode from here rather than making the user find the
        # coach button: they have already said what they want to do.
        context.user_data[CHAT_MODE_KEY] = True
        await query.edit_message_text(
            "✨ *בניית תוכנית אימונים*\n\n"
            "המאמן רואה את כל הנתונים שלך - האימונים האחרונים, עומס האימונים, "
            "השינה, ההתאוששות והמטרה שהגדרת - ובונה לפיהם תוכנית שבועית מותאמת.\n\n"
            "ספר לו מה אתה רוצה להשיג, וכמה פעמים בשבוע אתה יכול להתאמן. "
            "אפשר לבקש שינויים עד שהתוכנית מתאימה לך.\n\n"
            "*התוכנית תיכנס למעקב רק אחרי שתאשר אותה.*",
            parse_mode="Markdown",
        )
        await query.message.chat.send_message(
            "כתוב למאמן מה אתה רוצה 👇", reply_markup=CHAT_KEYBOARD
        )
        return

    if action == "delete":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            plans = [(p.discipline, discipline_label(p.discipline)) for p in active_plans(session, user.id)]

        if not plans:
            await query.edit_message_text("אין תוכנית למחוק.")
            return

        if len(plans) == 1:
            discipline, label = plans[0]
            await query.edit_message_text(
                f"למחוק את תוכנית ה{label}?\n\nאי אפשר לשחזר.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("🗑 כן, מחק", callback_data=f"plancancel:confirm:{discipline}"),
                            InlineKeyboardButton("ביטול", callback_data="plancancel:abort"),
                        ]
                    ]
                ),
            )
            return

        await query.edit_message_text(
            "איזו תוכנית למחוק?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(f"🗑 {label}", callback_data=f"plancancel:{key}")]
                    for key, label in plans
                ]
                + [[InlineKeyboardButton("ביטול", callback_data="plancancel:abort")]]
            ),
        )
        return

    if action == "approve":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            approved = approve_plans(session, user.id)
            week = format_upcoming_week(session, user.id)
        if not approved:
            await query.edit_message_text("אין תוכנית שממתינה לאישור.")
            return
        await query.edit_message_text("✅ *התוכנית אושרה ונכנסה למעקב*", parse_mode="Markdown")
        await _reply_plain(query.message.chat, week, reply_markup=MAIN_KEYBOARD)
        return

    if action == "discard":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            discard_pending(session, user.id)
        await query.edit_message_text("התוכנית לא נשמרה. אפשר לבקש מהמאמן אחרת 🙂")
        return


async def delete_plan_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Asks which plan when there is more than one, and confirms either way.
    Never deletes on the strength of this tap alone.
    """
    user_id = await _require_active(update)
    if user_id is None:
        return

    with Session(engine) as session:
        plans = [(p.discipline, discipline_label(p.discipline)) for p in active_plans(session, user_id)]

    if not plans:
        await update.message.reply_text("אין לך תוכנית אימונים למחוק.", reply_markup=MAIN_KEYBOARD)
        return

    if len(plans) == 1:
        # One plan, so nothing to choose - go straight to the confirmation
        # rather than making the user pick from a list of one.
        discipline, label = plans[0]
        await update.message.reply_text(
            f"למחוק את תוכנית ה{label}?\n\nאי אפשר לשחזר.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🗑 כן, מחק", callback_data=f"plancancel:confirm:{discipline}"),
                        InlineKeyboardButton("ביטול", callback_data="plancancel:abort"),
                    ]
                ]
            ),
        )
        return

    await update.message.reply_text(
        "איזו תוכנית למחוק?",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"🗑 {label}", callback_data=f"plancancel:{key}")]
                for key, label in plans
            ]
            + [[InlineKeyboardButton("ביטול", callback_data="plancancel:abort")]]
        ),
    )


async def on_plan_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Two taps to cancel: a plan is work the user did with the coach, and a
    single mis-tap losing it would be the same mistake the account-deletion
    button already avoids.
    """
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")

    if len(parts) == 2:
        discipline = parts[1]
        await query.edit_message_text(
            f"למחוק את תוכנית ה{discipline_label(discipline)}?\n\nאי אפשר לשחזר.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🗑 כן, מחק", callback_data=f"plancancel:confirm:{discipline}"),
                        InlineKeyboardButton("ביטול", callback_data="plancancel:abort"),
                    ]
                ]
            ),
        )
        return

    if parts[1] == "abort":
        await query.edit_message_text("לא בוטל כלום 👍")
        return

    discipline = parts[2]
    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        # Scoped by user id as well as discipline: the callback data naming
        # the plan comes from the client and must not be the only thing
        # deciding whose row gets deleted.
        removed = cancel_plan(session, user.id, discipline) if user else False

    await query.edit_message_text(
        f"🗑 תוכנית ה{discipline_label(discipline)} בוטלה." if removed
        else "לא נמצאה תוכנית לביטול."
    )
