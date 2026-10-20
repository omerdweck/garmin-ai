"""
/profile: everything the coach remembers about the athlete, readable and
deletable by the athlete.

Memory the user cannot see is memory they cannot correct - and a coach
building plans on a wrong fact ("only free on Saturdays" from a month ago)
does real harm. So every stored fact is shown, and every note has its own
delete button.
"""

from sqlmodel import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.bot.common import _find_user, _reply, _require_active
from app.db.session import engine
from app.models.plan_session import DAY_KEYS, DAY_NAMES_HE
from app.services.athlete_profile import clear_notes, delete_note, profile_as_dict

FIELD_LABELS = {
    "sports": "ענפים",
    "experience": "ניסיון",
    "training_days": "ימים פנויים לאימון",
    "max_session_minutes": "אורך אימון מקסימלי (דק')",
    "equipment": "ציוד",
    "pool_length_m": "אורך בריכה (מ')",
    "ftp_watts": "FTP (וואט)",
    "limitations": "מגבלות",
}
SPORT_LABELS = {
    "running": "ריצה", "cycling": "אופניים", "swimming": "שחייה", "strength": "כוח",
    "walking": "הליכה", "hiking": "טיול רגלי", "hiit": "HIIT", "yoga": "יוגה",
    "mobility": "מוביליטי", "other": "אחר",
}
EXPERIENCE_LABELS = {"beginner": "מתחיל", "intermediate": "בינוני", "advanced": "מתקדם"}


def _show_value(name, value) -> str:
    if name == "sports":
        return ", ".join(SPORT_LABELS.get(v, v) for v in value)
    if name == "training_days":
        return ", ".join(DAY_NAMES_HE[DAY_KEYS.index(d)] for d in value if d in DAY_KEYS)
    if name == "experience":
        return EXPERIENCE_LABELS.get(value, value)
    return str(value)


def _render(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["👤 *מה המאמן יודע עליך*", ""]
    if data["profile"]:
        for name, value in data["profile"].items():
            lines.append(f"• {FIELD_LABELS.get(name, name)}: {_show_value(name, value)}")
    else:
        lines.append("_עוד לא נשמר פרופיל. ספר למאמן בשיחה על הימים, הציוד והמטרות שלך._")

    buttons = []
    if data["notes"]:
        lines += ["", "📝 *דברים שהמאמן זוכר:*"]
        for index, note in enumerate(data["notes"], 1):
            lines.append(f"{index}. {note['text']}")
            buttons.append(InlineKeyboardButton(f"🗑 {index}", callback_data=f"profile:delnote:{note['id']}"))

    lines += ["", "_לשינוי פרטים - פשוט ספר למאמן בשיחה._"]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    if data["notes"]:
        rows.append([InlineKeyboardButton("🧹 מחק את כל הזיכרונות", callback_data="profile:clear")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = await _require_active(update)
    if user_id is None:
        return
    with Session(engine) as session:
        text, keyboard = _render(profile_as_dict(session, user_id))
    await _reply(update, text, reply_markup=keyboard)


async def on_profile_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")

    with Session(engine) as session:
        user = _find_user(session, query.message.chat_id)
        if user is None:
            await query.edit_message_text("עדיין לא התחלנו - שלח /start 🔗")
            return

        if parts[1] == "show":
            text, keyboard = _render(profile_as_dict(session, user.id))
            await _reply(update, text, reply_markup=keyboard)
            return

        if parts[1] == "delnote" and len(parts) == 3 and parts[2].isdigit():
            # delete_note is scoped to this user, so a forged id deletes nothing.
            delete_note(session, user.id, int(parts[2]))
        elif parts[1] == "clear":
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ כן, למחוק הכל", callback_data="profile:clear_confirm"),
                    InlineKeyboardButton("↩️ ביטול", callback_data="profile:show_inplace"),
                ]])
            )
            return
        elif parts[1] == "clear_confirm":
            clear_notes(session, user.id)

        text, keyboard = _render(profile_as_dict(session, user.id))

    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        # Markdown in a note the user dictated can be unbalanced.
        await query.edit_message_text(text, reply_markup=keyboard)
