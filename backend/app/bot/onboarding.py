"""
/start, terms, the join request and owner approval, and linking a Garmin account.

Moved verbatim from the former single-file app/bot.py.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlmodel import Session, select
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)

from app.bot_texts import (
    ASK_EMAIL_TEXT,
    ASK_PASSWORD_TEXT,
    TERMS,
    TERMS_DECLINED,
    WELCOME,
)
from app.core.config import settings
from app.core.crypto import encrypt
from app.core.garmin_client import GarminAuthError, GarminRateLimitError, login_to_garmin
from app.db.session import engine
from app.models.garmin_account import GarminAccount
from app.models.user import User
from app.services.admin_view import (
    format_new_user_alert,
)
from app.models.join_request import STATUS_PENDING, STATUS_REJECTED
from app.services.signup_control import (
    create_or_refresh_request,
    decide,
    get_request,
    is_approved,
    pending_requests,
    rejection_reason,
)
# Aliased: activity_view exports a type_label too, and a bare import of both
# silently left whichever came last in force.
from app.services.account_lifecycle import (
    AccountState,
    get_account,
    resolve_state,
)
from app.tasks import backfill_user_history_task, sync_one_user_task
from app.bot.common import MAIN_KEYBOARD, _find_user, _reconnect_keyboard, _summary_hour_keyboard

logger = logging.getLogger(__name__)


AWAITING_TERMS, ASK_EMAIL, ASK_PASSWORD = range(3)

# Terms acceptance timestamp, held in memory across the onboarding
# conversation and written to the User row only once linking succeeds -
# same reason no User row is created before then.
TERMS_ACCEPTED_KEY = "terms_accepted_at"


# --------------------------------------------------------------------------
# Onboarding
# --------------------------------------------------------------------------


def _terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ מאשר", callback_data="terms:accept"),
                InlineKeyboardButton("❌ לא מאשר", callback_data="terms:decline"),
            ]
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point for every state. Which of the five it lands in decides how
    much of onboarding actually runs - a returning user should never be
    asked to redo a step they've already completed.
    """
    with Session(engine) as session:
        state, _ = resolve_state(session, update.effective_chat.id)

    if state is AccountState.ACTIVE:
        await update.message.reply_text(
            "שלום שוב! 👋 החשבון שלך מחובר ומוכן.\nבחר פעולה מהתפריט למטה 👇",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    if state is AccountState.DISCONNECTED:
        await update.message.reply_text(
            "🔌 *החשבון שלך מנותק כרגע*\n\n"
            "כל הנתונים וההגדרות שלך שמורים - אין צורך להזין סיסמה מחדש.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        await update.effective_chat.send_message("מוכן לחזור? 👇", reply_markup=_reconnect_keyboard())
        return ConversationHandler.END

    if state is AccountState.NEEDS_LINK:
        # Terms already accepted and the user row already exists - the only
        # thing missing is a working credential, so skip straight to it.
        await update.message.reply_text(
            "🔗 *צריך לחבר מחדש את חשבון הגרמין*\n\n"
            "אנחנו זוכרים אותך ואת כל הנתונים שלך - רק ההתחברות לגרמין צריכה חידוש.\n\n"
            "📧 מה כתובת המייל שלך ב-Garmin Connect?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ASK_EMAIL

    # Strip the menu keyboard for the rest of onboarding: the only valid
    # actions from here are the inline buttons, and leaving a menu on
    # screen would invite taps that can't be honored yet.
    if state is AccountState.NEEDS_TERMS:
        await update.message.reply_text(
            "📋 *כדי להמשיך צריך לאשר את תנאי השימוש*\n\nאנחנו זוכרים אותך, רק האישור חסר.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        # AccountState.UNKNOWN - a genuinely new person. Both gates apply
        # here and nowhere else: someone already registered must never be
        # locked out by a lowered cap or a changed approval.
        with Session(engine) as session:
            refusal = rejection_reason(session)
            approved = is_approved(session, update.effective_chat.id)
            existing = get_request(session, update.effective_chat.id)

        if refusal is not None:
            await update.message.reply_text(refusal, reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

        if not approved:
            await _prompt_for_access(update, existing)
            return ConversationHandler.END

        await update.message.reply_text(WELCOME, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())

    await update.effective_chat.send_message(TERMS, parse_mode="Markdown", reply_markup=_terms_keyboard())
    return AWAITING_TERMS


async def _prompt_for_access(update: Update, existing) -> None:
    """
    What an unapproved newcomer sees. Ends the conversation rather than
    holding them in a state: approval arrives minutes or hours later, out of
    band, and a conversation waiting on it would be a mode they are stuck in.
    They send /start again once told they are in.
    """
    if existing is not None and existing.status == STATUS_PENDING:
        await update.message.reply_text(
            "⏳ *הבקשה שלך נשלחה וממתינה לאישור*\n\nתקבל הודעה ברגע שהיא תאושר.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if existing is not None and existing.status == STATUS_REJECTED:
        # No retry button: someone told no should not be able to re-ask their
        # way in by tapping again. The owner can still change their mind.
        await update.message.reply_text(
            "הבקשה שלך להצטרפות לא אושרה.\n\nאם לדעתך זו טעות - פנה למי שנתן לך את הקישור.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await update.message.reply_text(
        "🔒 *הבוט הזה פרטי*\n\n"
        "כדי להצטרף צריך אישור של מי שמפעיל אותו.\n"
        "אם הוזמנת - שלח בקשה והוא יקבל התראה 👇",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.effective_chat.send_message(
        "מוכן?",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🙋 בקש גישה", callback_data="join:request")]]
        ),
    )


async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Records the request and puts it in front of the owner."""
    query = update.callback_query
    await query.answer()

    chat = update.effective_chat
    user = update.effective_user

    with Session(engine) as session:
        if rejection_reason(session) is not None or is_approved(session, chat.id):
            # Capacity filled, or they were approved between tapping and now.
            await query.edit_message_text("שלח /start כדי להמשיך.")
            return

        display_name = " ".join(filter(None, [user.first_name, user.last_name])) or None
        request = create_or_refresh_request(session, chat.id, display_name, user.username)
        if request.status == STATUS_REJECTED:
            await query.edit_message_text("הבקשה שלך להצטרפות לא אושרה.")
            return

        pending = len(pending_requests(session))

    await query.edit_message_text(
        "✅ הבקשה נשלחה.\n\nתקבל הודעה ברגע שהיא תאושר."
    )

    # Everything shown to the owner except the chat id is chosen by the
    # requester - a display name is not identity. The id is the part nobody
    # can pick, which is why it is shown alongside.
    handle = f"@{user.username}" if user.username else "אין שם משתמש"
    try:
        await context.bot.send_message(
            settings.admin_chat_id,
            "🙋 *בקשת הצטרפות חדשה*\n\n"
            f"שם: {display_name or 'לא צוין'}\n"
            f"יוזר: {handle}\n"
            f"מזהה: `{chat.id}`\n\n"
            f"_ממתינות: {pending}_\n"
            "⚠️ השם והיוזר נבחרים על ידי המבקש. אשר רק אם אתה מזהה.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ אשר", callback_data=f"join_decide:approve:{chat.id}"),
                        InlineKeyboardButton("❌ דחה", callback_data=f"join_decide:reject:{chat.id}"),
                    ]
                ]
            ),
        )
    except Exception:
        # The request is already stored, so /admin requests still surfaces it.
        logger.exception("Could not notify admin of join request from %s", chat.id)


async def on_join_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only. The callback_data carries the target chat id."""
    query = update.callback_query

    if not settings.is_admin_configured or update.effective_chat.id != settings.admin_chat_id:
        # Someone who guessed the callback format. Answer nothing useful.
        await query.answer()
        logger.warning("Ignoring join decision from non-admin chat %s", update.effective_chat.id)
        return

    await query.answer()
    _, action, raw_chat_id = query.data.split(":", 2)
    target = int(raw_chat_id)
    approved = action == "approve"

    with Session(engine) as session:
        request = decide(session, target, approved)
        if request is None:
            await query.edit_message_text("הבקשה כבר לא קיימת.")
            return
        name = request.display_name or str(target)

    await query.edit_message_text(
        f"{'✅ אושר' if approved else '❌ נדחה'}: {name} (`{target}`)",
        parse_mode="Markdown",
    )

    try:
        if approved:
            await context.bot.send_message(
                target,
                "🎉 *הבקשה שלך אושרה!*\n\nשלח /start כדי להתחיל.",
                parse_mode="Markdown",
            )
        else:
            await context.bot.send_message(
                target,
                "הבקשה שלך להצטרפות לא אושרה.",
            )
    except Exception:
        # A user who blocked the bot or deleted the chat. The decision stands.
        logger.warning("Could not notify chat %s of decision", target, exc_info=True)


async def on_terms_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "terms:decline":
        # Editing the original message removes the buttons, so a declined
        # prompt can't be re-answered from scrollback later.
        await query.edit_message_text(TERMS_DECLINED)
        return ConversationHandler.END

    accepted_at = datetime.now(timezone.utc)
    context.user_data[TERMS_ACCEPTED_KEY] = accepted_at
    await query.edit_message_text("📋 תנאי השימוש אושרו ✅")

    # An already-known user (e.g. one who predates the terms screen) gets
    # the acceptance written immediately - there's no pending link step to
    # defer it to, and their Garmin may already be connected.
    with Session(engine) as session:
        state, user = resolve_state(session, query.message.chat_id)
        if user is not None and user.terms_accepted_at is None:
            user.terms_accepted_at = accepted_at
            session.add(user)
            session.commit()
        account_exists = user is not None and get_account(session, user.id) is not None

    if account_exists:
        await query.message.chat.send_message(
            "🎉 *הכל מוכן!*\n\nהחשבון שלך כבר מחובר - אפשר להתחיל 👇",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    await query.message.chat.send_message(ASK_EMAIL_TEXT, parse_mode="Markdown")
    return ASK_EMAIL


async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Held only in memory for the next message; never written to the DB
    # unless the login actually succeeds.
    # Lower-cased because phone keyboards auto-capitalise the first letter,
    # and the user shouldn't have to notice. Nothing here depends on the
    # original casing: this address is only ever sent to Garmin's login and
    # is never stored (the bot identifies people by telegram_chat_id).
    context.user_data["garmin_email"] = update.message.text.strip().lower()
    await update.message.reply_text(ASK_PASSWORD_TEXT, parse_mode="Markdown")
    return ASK_PASSWORD


async def do_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = context.user_data.pop("garmin_email", None)
    password = update.message.text
    chat_id = update.effective_chat.id

    # Delete the message carrying the password right away, success or not -
    # it must not sit around in the chat history.
    await update.message.delete()
    await update.effective_chat.send_message("🔄 מתחבר לגרמין… זה יכול לקחת עד כדקה.")

    try:
        token_bundle = await asyncio.to_thread(login_to_garmin, email, password)
    except GarminAuthError:
        await update.effective_chat.send_message(
            "❌ פרטי ההתחברות לגרמין שגויים.\nשלח /start כדי לנסות שוב."
        )
        return ConversationHandler.END
    except GarminRateLimitError:
        await update.effective_chat.send_message(
            "⏳ גרמין חוסמים כרגע ניסיונות התחברות (הגבלה זמנית מצדם).\n"
            "נסה שוב בעוד כמה דקות עם /start."
        )
        return ConversationHandler.END

    # Only now, after a *successful* login, does anything get persisted -
    # a failed attempt leaves no row behind to clutter the DB.
    with Session(engine) as session:
        user = _find_user(session, chat_id)
        if user is None:
            user = User(
                telegram_chat_id=chat_id,
                is_active=True,
                terms_accepted_at=context.user_data.get(TERMS_ACCEPTED_KEY),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        elif user.terms_accepted_at is None:
            # Re-linking after an unlink still goes through the terms
            # screen, so record it if this is the first time we've captured it.
            user.terms_accepted_at = context.user_data.get(TERMS_ACCEPTED_KEY)
            session.add(user)
            session.commit()

        existing = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()
        if existing is not None:
            existing.encrypted_token = encrypt(token_bundle)
            existing.linked_at = datetime.now(timezone.utc)
            session.add(existing)
        else:
            session.add(GarminAccount(user_id=user.id, encrypted_token=encrypt(token_bundle)))
        session.commit()
        user_id = user.id

    # No menu keyboard yet - onboarding has one step left (the summary
    # hour), and the menu is what signals "setup is done".
    await update.effective_chat.send_message(
        "✅ *מעולה, החשבון חובר בהצלחה!*\n\nמושך עכשיו את הנתונים שלך מגרמין 🔄",
        parse_mode="Markdown",
    )
    # Two tasks on purpose: the quick one makes today's data available in
    # seconds (and reports back with the first metrics, so the user sees
    # something concrete right after linking), the backfill fills in a month
    # of history behind it.
    sync_one_user_task.delay(user_id, notify_on_success=True)
    backfill_user_history_task.delay(user_id)

    # Tell the owner immediately. Finding out from the monthly invoice that
    # strangers have been using the bot is exactly the situation the usage
    # tracking exists to prevent, and a ceiling nobody looks at is not a
    # control. Best-effort: a failed notification must not derail a
    # successful signup.
    if settings.is_admin_configured and settings.admin_chat_id != update.effective_chat.id:
        try:
            with Session(engine) as admin_session:
                new_user = admin_session.get(User, user_id)
                await context.bot.send_message(
                    settings.admin_chat_id,
                    format_new_user_alert(admin_session, new_user),
                    parse_mode="Markdown",
                )
        except Exception:
            logger.warning("Could not notify admin of new user %s", user_id, exc_info=True)

    await update.effective_chat.send_message(
        "🔔 *דבר אחרון* - אני יכול לשלוח לך סיכום קצר בסוף כל יום: "
        "מה עשית, איך הגוף הגיב, ומה כדאי לתכנן למחר.\n\n"
        "באיזו שעה תרצה לקבל אותו?",
        parse_mode="Markdown",
        reply_markup=_summary_hour_keyboard(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("בוטל. שלח /start כדי להתחיל מחדש 🔄")
    return ConversationHandler.END
