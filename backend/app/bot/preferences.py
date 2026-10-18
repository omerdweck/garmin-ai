"""
Settings: summary hours, weekly summary, account disconnect/reconnect/delete.

Moved verbatim from the former single-file app/bot.py.
"""

from sqlmodel import Session
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    ContextTypes,
)

from app.db.session import engine
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.account_lifecycle import (
    delete_user_completely,
    disconnect,
    reconnect,
)
from app.services.metrics_view import (
    format_status,
)
from app.tasks import sync_one_user_task
from app.bot.common import MAIN_KEYBOARD, _find_user, _reconnect_keyboard, _reply, _settings_keyboard, _summary_hour_keyboard



WEEKLY_DAY_NAMES = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]
WEEKLY_HOUR_CHOICES = [8, 10, 12, 18, 19, 20, 21]


async def on_weekly_setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Day first, then hour. Two steps rather than a grid: seven days times
    seven hours is forty-nine buttons, which is a wall, and nobody picks an
    hour without having decided a day.
    """
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")

    if parts[1] == "off":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            user.weekly_summary_day = None
            user.weekly_summary_hour = None
            session.add(user)
            session.commit()
        await query.edit_message_text("🔕 הסיכום השבועי כובה.")
        return

    if parts[1] == "day":
        # Held in the callback data of the hour buttons rather than in
        # user_data: the choice survives a bot restart between the two taps,
        # and there is no draft left behind if the user walks away.
        day = int(parts[2])
        buttons = [
            InlineKeyboardButton(f"{h:02d}:00", callback_data=f"weekly:set:{day}:{h}")
            for h in WEEKLY_HOUR_CHOICES
        ]
        rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
        rows.append([InlineKeyboardButton("⬅️ יום אחר", callback_data="weekly:menu")])
        await query.edit_message_text(
            f"📅 *יום {WEEKLY_DAY_NAMES[day]}* - באיזו שעה?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if parts[1] == "set":
        day, hour = int(parts[2]), int(parts[3])
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            user.weekly_summary_day = day
            user.weekly_summary_hour = hour
            session.add(user)
            session.commit()
        await query.edit_message_text(
            f"✅ סיכום שבועי כל יום *{WEEKLY_DAY_NAMES[day]}* בשעה *{hour:02d}:00*",
            parse_mode="Markdown",
        )
        return

    # weekly:menu
    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        current = (user.weekly_summary_day, user.weekly_summary_hour)

    when = (
        f"{WEEKLY_DAY_NAMES[current[0]]} ב-{current[1]:02d}:00"
        if current[0] is not None and current[1] is not None
        else "כבוי"
    )
    buttons = [
        InlineKeyboardButton(name, callback_data=f"weekly:day:{i}")
        for i, name in enumerate(WEEKLY_DAY_NAMES)
    ]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton("🔕 בלי סיכום שבועי", callback_data="weekly:off")])
    await query.edit_message_text(
        "📅 *סיכום שבועי*\n\n"
        "טבלת מספרים של השבוע - אימונים, שינה, דופק מנוחה, עומס ועמידה בתוכנית, "
        "בהשוואה לשבוע הקודם - ואחריה קריאה קצרה של המאמן.\n\n"
        f"כרגע: *{when}*\n\n"
        "באיזה יום לשלוח?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with Session(engine) as session:
        user = _find_user(session, update.effective_chat.id)
        if user is None:
            await update.message.reply_text("עדיין לא התחלנו - שלח /start 🔗")
            return
        text = format_status(session, user)
    await _reply(update, text, reply_markup=_settings_keyboard())


# --------------------------------------------------------------------------
# Inline button callbacks
# --------------------------------------------------------------------------


async def on_summary_hour(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]

    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        if user is None:
            await query.edit_message_text("צריך קודם לחבר חשבון - שלח /start 🔗")
            return
        user.daily_summary_hour = None if choice == "off" else int(choice)
        session.add(user)
        session.commit()
        hour = user.daily_summary_hour

    if hour is None:
        await query.edit_message_text("🔕 בסדר, לא אשלח סיכום יומי.\nתמיד אפשר להפעיל דרך ⚙️ הגדרות.")
    else:
        await query.edit_message_text(f"🔔 מצוין! אשלח לך סיכום יומי כל יום ב-{hour:02d}:00.")

    # Sending the menu here is what ends onboarding: it only appears once
    # terms are accepted, Garmin is linked and the summary preference is
    # set, so its arrival is the signal that setup is complete.
    await query.message.chat.send_message(
        "🎉 *הכל מוכן!*\n\nאפשר להתחיל - בחר מה שתרצה מהתפריט שלמטה 👇",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def on_settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]

    if action == "summary_hour":
        await query.message.reply_text(
            "🔔 באיזו שעה לשלוח את הסיכום היומי?",
            reply_markup=_summary_hour_keyboard(),
        )
        return

    if action == "unlink":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            if user is None:
                await query.edit_message_text("אין חשבון מקושר.")
                return
            disconnect(session, user.id)

        await query.edit_message_text(
            "🔌 *החשבון נותק*\n\n"
            "הסנכרון האוטומטי והסיכום היומי הופסקו.\n"
            "כל הנתונים, ההגדרות וההתחברות שלך *נשמרו* - חזרה היא לחיצה אחת, בלי סיסמה.",
            parse_mode="Markdown",
        )
        await query.message.chat.send_message(
            "רוצה לחזור? 👇", reply_markup=_reconnect_keyboard()
        )
        return

    if action == "delete":
        await query.message.chat.send_message(
            "⚠️ *מחיקת המשתמש*\n\n"
            "הפעולה תמחק לצמיתות את *כל* המידע שלך:\n"
            "• כל נתוני הבריאות והאימונים שנאספו\n"
            "• היסטוריית השיחות עם המאמן\n"
            "• החיבור לחשבון הגרמין וההגדרות שלך\n\n"
            "*לא ניתן לשחזר.* אם תרצה לחזור בעתיד, תצטרך לבקש הצטרפות מחדש ולחכות לאישור.\n\n"
            "האם אתה בטוח?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🗑 ברצוני למחוק", callback_data="account:delete_confirm")],
                    [InlineKeyboardButton("↩️ איני רוצה למחוק", callback_data="account:delete_cancel")],
                ]
            ),
        )


async def on_account_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    chat_id = query.message.chat_id

    if action == "reconnect":
        with Session(engine) as session:
            user = _find_user(session, chat_id)
            if user is None:
                await query.edit_message_text("שלח /start כדי להתחיל 👋")
                return
            user_id = user.id
            already_current = reconnect(session, user_id)

        await query.edit_message_text("✅ חזרת לפעולה!")
        if already_current:
            # Data already synced today - re-fetching it would spend time
            # and Garmin rate-limit budget to learn nothing new.
            await query.message.chat.send_message(
                "הנתונים שלך כבר מעודכנים להיום, אז אפשר להתחיל מיד 👇",
                reply_markup=MAIN_KEYBOARD,
            )
        else:
            sync_one_user_task.delay(user_id, notify_on_success=True)
            await query.message.chat.send_message(
                "🔄 מסנכרן את הנתונים העדכניים… אשלח לך אותם עוד רגע.",
                reply_markup=MAIN_KEYBOARD,
            )
        return

    if action == "delete_cancel":
        await query.edit_message_text("👍 לא נמחק כלום. החשבון שלך נשאר כמו שהוא.")
        return

    if action == "delete_confirm":
        with Session(engine) as session:
            user = _find_user(session, chat_id)
            if user is None:
                await query.edit_message_text("לא נמצא משתמש למחיקה.")
                return
            delete_user_completely(session, user.id)

        context.user_data.clear()
        await query.edit_message_text(
            "🗑 *הכל נמחק*\n\nכל המידע שלך הוסר מהמערכת לצמיתות.\n\n"
            "תודה שניסית! אם תרצה לחזור מתישהו - שלח /start ותוכל לבקש הצטרפות מחדש.",
            parse_mode="Markdown",
        )
        # Clear the menu too: leaving it up would offer actions that now
        # resolve to "unknown user".
        await query.message.chat.send_message("👋", reply_markup=ReplyKeyboardRemove())
