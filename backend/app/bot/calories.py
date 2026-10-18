"""
Calorie tracking: meals, the daily target, burn, and reminders.

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
from app.models.user import User
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.calorie_tracking import (
    MAX_TARGET,
    MIN_TARGET,
    add_entry,
    burned_on,
    format_balance,
    is_tracking,
    parse_calories,
    parse_target,
    reminder_hours,
    set_burn_override,
    set_reminder_hours,
    set_target,
    weekly_average_burn,
)
from app.services.meal_plan_view import delete_meal_plan, format_meal_plan
from app.bot.common import MAIN_KEYBOARD, _find_user, _reply, _reply_text, _require_active



CALORIE_KEY = "calorie_draft"

# Common amounts, so a typical meal is one tap. The ✏️ escape covers
# everything else - see the manual-activity flow for the same shape.
CALORIE_CHOICES = [200, 350, 500, 700, 900]
TARGET_CHOICES = [1600, 1800, 2000, 2200, 2500]

CALORIE_PROMPT = (
    "🍽 *כמה קלוריות?*\n\n"
    "כתוב מספר. לדוגמה:\n"
    "א. `450`\n"
    "ב. `450 קלוריות`\n"
    "ג. `1,200`"
)

TARGET_PROMPT = (
    "🎯 *מה יעד הקלוריות היומי שלך?*\n\n"
    "כמה קלוריות בממוצע ליום. לדוגמה:\n"
    "א. `1800`\n"
    "ב. `2200 קלוריות`\n\n"
    f"_בין {MIN_TARGET:,} ל-{MAX_TARGET:,}._"
)


def _calorie_menu(tracking: bool, burn_known: bool) -> InlineKeyboardMarkup:
    if not tracking:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("🎯 הגדרת יעד יומי", callback_data="cal:settarget")]]
        )

    rows = [
        [InlineKeyboardButton("➕ הוספת ארוחה", callback_data="cal:add")],
    ]
    # Only offered when there is actually a hole to fill. Showing it on a day
    # Garmin covered would invite overwriting a measurement with a guess.
    if not burn_known:
        rows.append(
            [
                InlineKeyboardButton("✏️ הזנת שריפה", callback_data="cal:burn"),
                InlineKeyboardButton("📊 ממוצע השבוע", callback_data="cal:burnavg"),
            ]
        )
    rows.append([InlineKeyboardButton("📖 התפריט שלי", callback_data="cal:menu")])
    rows.append([InlineKeyboardButton("⚙️ הגדרות מעקב", callback_data="cal:settings")])
    return InlineKeyboardMarkup(rows)


async def show_calories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The 🍽 screen. Straight from the database - no Claude, no tokens."""
    user_id = await _require_active(update)
    if user_id is None:
        return

    with Session(engine) as session:
        user = session.get(User, user_id)
        if not is_tracking(user):
            await update.message.reply_text(
                "🍽 *מעקב קלוריות*\n\n"
                "המעקב משווה בין כמות הקלוריות שנצרכה לבין ההוצאה הקלורית היומית.\n\n"
                "*צריכה* — מוזנת ידנית. בכל ארוחה מזינים את כמות הקלוריות, "
                "והמערכת מסכמת אותן לאורך היום.\n\n"
                "*הוצאה* — נאספת אוטומטית מחשבון הגרמין שלך, וכוללת את סך ההוצאה "
                "היומית: חילוף חומרים בסיסי, פעילות יומיומית ואימונים.\n\n"
                "בנוסף ניתן להגדיר תזכורות יומיות להזנה, ולבקש מהמאמן תפריט אישי "
                "המותאם ליעד.",
                parse_mode="Markdown",
                reply_markup=_calorie_menu(tracking=False, burn_known=False),
            )
            return

        text = format_balance(session, user_id)
        burned, _ = burned_on(session, user_id)

    await _reply(update, text, reply_markup=_calorie_menu(True, burned is not None))


def _amount_keyboard(choices: list[int], prefix: str, extra: list = None) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"{c:,}", callback_data=f"cal:{prefix}:{c}") for c in choices
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("✏️ להקליד מספר אחר", callback_data=f"cal:type:{prefix}")])
    rows += extra or []
    rows.append([InlineKeyboardButton("ביטול", callback_data="cal:abort")])
    return InlineKeyboardMarkup(rows)


async def on_calorie_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every button in the calorie flow. Same one-handler shape as on_manual_step."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    step = parts[1]
    draft = context.user_data.setdefault(CALORIE_KEY, {})

    if step == "abort":
        context.user_data.pop(CALORIE_KEY, None)
        await query.edit_message_text("בוטל 👍")
        await query.message.chat.send_message("חזרה לתפריט 👇", reply_markup=MAIN_KEYBOARD)
        return

    if step == "settarget":
        await query.edit_message_text(
            "🎯 *בחר יעד קלוריות יומי*\n\n_תמיד אפשר לשנות אחר כך._",
            parse_mode="Markdown",
            reply_markup=_amount_keyboard(TARGET_CHOICES, "target"),
        )
        return

    if step == "type":
        draft["awaiting"] = parts[2]
        prompt = TARGET_PROMPT if parts[2] == "target" else CALORIE_PROMPT
        await query.edit_message_text(prompt, parse_mode="Markdown")
        return

    if step == "target":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            set_target(session, user.id, int(parts[2]))
        context.user_data.pop(CALORIE_KEY, None)
        await query.edit_message_text(
            f"✅ היעד נקבע: *{int(parts[2]):,}* קלוריות ליום", parse_mode="Markdown"
        )
        await query.message.chat.send_message(
            "עכשיו אפשר להתחיל להזין מה שאכלת 🍽\n\n"
            "_כדאי גם להגדיר תזכורות - הזנה ידנית קל לשכוח._",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    if step == "add":
        await query.edit_message_text(
            "🍽 כמה קלוריות היו בארוחה?",
            reply_markup=_amount_keyboard(CALORIE_CHOICES, "amount"),
        )
        return

    if step == "amount":
        await _save_calories(query, context, int(parts[2]))
        return

    if step == "burn":
        draft["awaiting"] = "burn"
        await query.edit_message_text(
            "🔥 *כמה קלוריות שרפת היום?*\n\n"
            "כתוב מספר, למשל `2300`.\n\n"
            "_זה סך ההוצאה היומית, לא רק האימון._",
            parse_mode="Markdown",
        )
        return

    if step == "burnavg":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            average = weekly_average_burn(session, user.id)
            if average is None:
                await query.edit_message_text(
                    "אין מספיק נתונים מהשבוע האחרון כדי לחשב ממוצע 🤔"
                )
                return
            set_burn_override(session, user.id, average)
            text = format_balance(session, user.id)
        await _reply_text(query, "📊 השתמשתי בממוצע השבוע שלך.\n\n" + text)
        return

    if step == "menu":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            text = format_meal_plan(session, user.id)
        if not text:
            await query.edit_message_text(
                "עוד אין לך תפריט 🍽\n\n"
                "לחץ 💬 שיחה עם המאמן ובקש שיבנה לך אחד לפי היעד שלך."
            )
            return
        await _reply_text(query, text)
        await query.message.chat.send_message(
            "​",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🗑 מחיקת התפריט", callback_data="cal:delmenu")]]
            ),
        )
        return

    if step == "delmenu":
        await query.edit_message_text(
            "למחוק את התפריט?\n\nאי אפשר לשחזר.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🗑 כן, מחק", callback_data="cal:delmenuok"),
                        InlineKeyboardButton("ביטול", callback_data="cal:abort"),
                    ]
                ]
            ),
        )
        return

    if step == "delmenuok":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            removed = delete_meal_plan(session, user.id)
        await query.edit_message_text("🗑 התפריט נמחק." if removed else "לא נמצא תפריט.")
        return

    if step == "settings":
        await _calorie_settings(query)
        return

    if step == "stop":
        await query.edit_message_text(
            "לבטל את מעקב הקלוריות?\n\nהרישומים שלך יישמרו.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔕 כן, בטל", callback_data="cal:stopconfirm"),
                        InlineKeyboardButton("ביטול", callback_data="cal:abort"),
                    ]
                ]
            ),
        )
        return

    if step == "stopconfirm":
        with Session(engine) as session:
            user = _find_user(session, query.message.chat_id)
            set_target(session, user.id, None)
        context.user_data.pop(CALORIE_KEY, None)
        await query.edit_message_text(
            "🔕 מעקב הקלוריות בוטל. הרישומים נשמרו.\n\n"
            "אפשר להפעיל מחדש בכל רגע מכפתור 🍽 קלוריות."
        )
        await query.message.chat.send_message("👇", reply_markup=MAIN_KEYBOARD)
        return


REMINDER_HOUR_CHOICES = [8, 10, 12, 14, 16, 18, 20, 22]


async def on_calorie_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Up to three reminder hours, toggled on and off in place.

    A toggle rather than a wizard because the set is small and the user is
    editing a collection, not answering a sequence of questions - tapping the
    hour that is already on should remove it, which a step-by-step flow makes
    awkward.
    """
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")

    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        hours = reminder_hours(user)

        if parts[1] == "toggle":
            hour = int(parts[2])
            if hour in hours:
                hours.remove(hour)
            elif len(hours) < 3:
                hours.append(hour)
            else:
                await query.answer("אפשר עד שלוש תזכורות", show_alert=True)
            set_reminder_hours(session, user.id, hours)
            hours = reminder_hours(session.get(User, user.id))

        elif parts[1] == "clear":
            set_reminder_hours(session, user.id, [])
            hours = []

    buttons = [
        InlineKeyboardButton(
            f"{'✅ ' if h in hours else ''}{h:02d}:00", callback_data=f"calrem:toggle:{h}"
        )
        for h in REMINDER_HOUR_CHOICES
    ]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton("🔕 בלי תזכורות", callback_data="calrem:clear")])
    rows.append([InlineKeyboardButton("סיימתי", callback_data="cal:settings")])

    chosen = "  ·  ".join(f"{h:02d}:00" for h in hours) if hours else "אין"
    await query.edit_message_text(
        f"🔔 *מתי להזכיר לך להזין קלוריות?*\n\n"
        f"בחר עד שלוש שעות. נבחרו: *{chosen}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _handle_calorie_text(update, context, draft: dict, text: str) -> None:
    """A typed target, meal or burn figure. Every path clears `awaiting`."""
    field = draft["awaiting"]

    if field == "target":
        value = parse_target(text)
        if value is None:
            await update.message.reply_text(
                "לא הצלחתי לקרוא את זה 🤔\n\n" + TARGET_PROMPT, parse_mode="Markdown"
            )
            return
        with Session(engine) as session:
            user = _find_user(session, update.effective_chat.id)
            set_target(session, user.id, value)
        context.user_data.pop(CALORIE_KEY, None)
        await update.message.reply_text(
            f"✅ היעד נקבע: *{value:,}* קלוריות ליום", parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    value = parse_calories(text)
    if value is None:
        await update.message.reply_text(
            "לא הצלחתי לקרוא את זה 🤔\n\n" + CALORIE_PROMPT, parse_mode="Markdown"
        )
        return

    with Session(engine) as session:
        user = _find_user(session, update.effective_chat.id)
        if field == "burn":
            set_burn_override(session, user.id, value)
            prefix = f"🔥 נרשמה שריפה של *{value:,}* קלוריות\n\n"
        else:
            add_entry(session, user.id, value)
            prefix = f"✅ נוספו *{value:,}* קלוריות\n\n"
        body = format_balance(session, user.id)

    context.user_data.pop(CALORIE_KEY, None)
    try:
        await update.message.reply_text(prefix + body, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
    except BadRequest:
        await update.message.reply_text(prefix + body, reply_markup=MAIN_KEYBOARD)


async def _save_calories(query, context, calories: int) -> None:
    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        add_entry(session, user.id, calories)
        text = format_balance(session, user.id)
    context.user_data.pop(CALORIE_KEY, None)
    await _reply_text(query, f"✅ נוספו *{calories:,}* קלוריות\n\n" + text)


async def _calorie_settings(query) -> None:
    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        hours = reminder_hours(user)
        target = user.daily_calorie_target

    when = "  ·  ".join(f"{h:02d}:00" for h in hours) if hours else "אין"
    await query.edit_message_text(
        f"⚙️ *הגדרות מעקב קלוריות*\n\n"
        f"יעד יומי: *{target:,}*\n"
        f"תזכורות: {when}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🎯 שינוי יעד", callback_data="cal:settarget")],
                [InlineKeyboardButton("🔔 תזכורות", callback_data="calrem:menu")],
                [InlineKeyboardButton("🔕 ביטול המעקב", callback_data="cal:stop")],
            ]
        ),
    )
