"""
Owner-only /admin: usage report, join requests, quotas and deletion.

Moved verbatim from the former single-file app/bot.py.
"""
import logging

from sqlmodel import Session
from telegram import (
    Update,
)
from telegram.ext import (
    ContextTypes,
)

from app.core.config import settings
from app.db.session import engine
from app.models.user import User
from app.services.admin_view import (
    format_admin_summary,
    format_admin_users,
    format_pending_requests,
    set_user_limit,
)
from app.services.signup_control import (
    decide,
)
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.account_lifecycle import (
    delete_user_completely,
)

logger = logging.getLogger(__name__)



async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Owner-only usage reporting and user management.

    A non-owner gets silence, not a refusal: "you are not authorised" would
    confirm the command exists and invite probing. As far as anyone else is
    concerned there is no /admin. For the same reason it is left out of
    set_my_commands, so it never appears in anyone's command menu.
    """
    chat_id = update.effective_chat.id
    if not settings.is_admin_configured or chat_id != settings.admin_chat_id:
        logger.info("Ignoring /admin from non-admin chat %s", chat_id)
        return

    args = context.args or []
    # Collected inside the session and sent after it closes: a Telegram call
    # is network I/O and has no business inside a database transaction.
    pending_notifications: list[tuple[int, bool]] = []

    with Session(engine) as session:
        if not args:
            text = format_admin_summary(session)

        elif args[0] == "users":
            text = format_admin_users(session)

        elif args[0] == "requests":
            text = format_pending_requests(session)

        elif args[0] in ("approve", "reject") and len(args) == 2:
            # The same decision the inline buttons make, reachable when the
            # notification carrying them has been swiped away.
            try:
                target = int(args[1])
            except ValueError:
                text = "❌ שימוש: `/admin approve <chat_id>`"
            else:
                approved = args[0] == "approve"
                decided = decide(session, target, approved)
                if decided is None:
                    text = f"❌ אין בקשה מ-{target}"
                else:
                    text = f"{'✅ אושר' if approved else '❌ נדחה'}: {target}"
                    pending_notifications.append((target, approved))

        elif args[0] == "limit" and len(args) == 3:
            try:
                text = set_user_limit(session, int(args[1]), int(args[2]))
            except ValueError:
                text = "❌ שימוש: `/admin limit <user_id> <מספר>`"

        elif args[0] == "delete" and len(args) == 2:
            # No confirmation step here on purpose: this is a typed command
            # available to one chat, and the destructive path users reach
            # through the menu already has its own two-button confirmation.
            try:
                target = int(args[1])
            except ValueError:
                text = "❌ שימוש: `/admin delete <user_id>`"
            else:
                target_user = session.get(User, target)
                if target_user is None:
                    text = f"❌ אין משתמש {target}"
                # Compared by chat id, not user id: `target` is our database
                # id and effective_user.id is Telegram's, so comparing those
                # two never matched and the guard protected nothing.
                elif target_user.telegram_chat_id == update.effective_chat.id:
                    text = "❌ לא ניתן למחוק את חשבון המנהל דרך הפקודה הזו."
                else:
                    deleted_chat = delete_user_completely(session, target)
                    if deleted_chat is not None:
                        # Half-filled forms and chat mode, held in memory.
                        context.application.drop_user_data(deleted_chat)
                    text = f"🗑 משתמש {target} נמחק על כל נתוניו, כולל אישור ההצטרפות."
        else:
            text = (
                "*פקודות ניהול*\n"
                "`/admin` - סיכום\n"
                "`/admin users` - רשימת משתמשים\n"
                "`/admin requests` - בקשות הצטרפות ממתינות\n"
                "`/admin approve <chat_id>` - אישור בקשה\n"
                "`/admin reject <chat_id>` - דחיית בקשה\n"
                "`/admin limit <id> <n>` - מכסה יומית (0 = חסימה)\n"
                "`/admin delete <id>` - מחיקה מלאה"
            )

    await update.message.reply_text(text, parse_mode="Markdown")

    for target, approved in pending_notifications:
        try:
            await context.bot.send_message(
                target,
                "🎉 *הבקשה שלך אושרה!*\n\nשלח /start כדי להתחיל." if approved
                else "הבקשה שלך להצטרפות לא אושרה.",
                parse_mode="Markdown",
            )
        except Exception:
            logger.warning("Could not notify chat %s of decision", target, exc_info=True)
