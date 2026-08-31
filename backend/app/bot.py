"""
The Telegram bot - runs as its own process (docker-compose `bot`
service), separate from FastAPI and Celery. Uses long polling (the bot
actively asks Telegram "anything new?" in a loop) rather than webhooks,
since webhooks need a public HTTPS URL Telegram can reach and we don't
have one yet - switch to webhooks once there's a real deployment.

Onboarding conversation (/start): find-or-create a User by
telegram_chat_id, then - if no Garmin account is linked yet - ask for
Garmin email then password, and call the *same* login_to_garmin()/
encrypt() used by the website's POST /garmin/link. The message
containing the password is deleted from the chat immediately after
being read; it's never logged or stored anywhere.
"""

import asyncio
import logging

from sqlmodel import Session, select
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.core.config import settings
from app.core.crypto import encrypt
from app.core.garmin_client import GarminAuthError, GarminRateLimitError, login_to_garmin
from app.db.session import engine
from app.models.garmin_account import GarminAccount
from app.models.user import User
from app.tasks import sync_one_user_task

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ASK_EMAIL, ASK_PASSWORD = range(2)


def _get_or_create_user(session: Session, chat_id: int) -> User:
    user = session.exec(select(User).where(User.telegram_chat_id == chat_id)).first()
    if user is None:
        user = User(telegram_chat_id=chat_id, is_active=True)
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def _has_linked_garmin(session: Session, user_id: int) -> bool:
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user_id)).first()
    return account is not None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id

    with Session(engine) as session:
        user = _get_or_create_user(session, chat_id)
        already_linked = _has_linked_garmin(session, user.id)

    if already_linked:
        await update.message.reply_text("החשבון שלך כבר מקושר לגרמין. אפשר פשוט לדבר איתי.")
        return ConversationHandler.END

    await update.message.reply_text(
        "בוא נקשר את חשבון הגרמין שלך.\nמה כתובת המייל שאיתה אתה מתחבר לגרמין?"
    )
    return ASK_EMAIL


async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["garmin_email"] = update.message.text.strip()
    await update.message.reply_text("תודה. עכשיו שלח את הסיסמה לגרמין (ההודעה תימחק מיד אחרי שאקרא אותה).")
    return ASK_PASSWORD


async def do_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = context.user_data.pop("garmin_email", None)
    password = update.message.text

    # Delete the message carrying the password right away, success or not -
    # it must not sit around in the chat history.
    await update.message.delete()

    # Garmin login can take anywhere from a couple of seconds to ~40s+ if it
    # has to fall through several retry strategies - without this, the chat
    # goes silent that whole time and looks broken.
    await update.effective_chat.send_message("מתחבר לגרמין… זה יכול לקחת עד כדקה.")

    try:
        token_bundle = await asyncio.to_thread(login_to_garmin, email, password)
    except GarminAuthError:
        await update.effective_chat.send_message("פרטי ההתחברות לגרמין שגויים. שלח /start כדי לנסות שוב.")
        return ConversationHandler.END
    except GarminRateLimitError:
        await update.effective_chat.send_message("גרמין חוסמים זמנית ניסיונות התחברות. נסה שוב בעוד כמה דקות עם /start.")
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    with Session(engine) as session:
        user = _get_or_create_user(session, chat_id)
        existing = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()
        if existing is not None:
            existing.encrypted_token = encrypt(token_bundle)
        else:
            session.add(GarminAccount(user_id=user.id, encrypted_token=encrypt(token_bundle)))
        session.commit()

    await update.effective_chat.send_message("החשבון קושר בהצלחה! אפשר לדבר איתי על הנתונים שלך.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("בוטל. שלח /start כדי להתחיל מחדש.")
    return ConversationHandler.END


async def sync_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Manual sync trigger. Enqueues the *same* Celery task the twice-daily
    schedule uses (see app/tasks.py) rather than calling the sync logic
    directly here, so both paths share one retry/notification behavior -
    if the stored token turns out to be invalid, sync_one_user_task itself
    sends the "please reconnect" message, whether it got here via this
    command or the scheduled job.
    """
    chat_id = update.effective_chat.id

    with Session(engine) as session:
        user = session.exec(select(User).where(User.telegram_chat_id == chat_id)).first()
        if user is None or not _has_linked_garmin(session, user.id):
            await update.message.reply_text("עדיין אין חשבון גרמין מקושר. שלח /start כדי לקשר אחד.")
            return
        user_id = user.id

    sync_one_user_task.delay(user_id)
    await update.message.reply_text("הסנכרון התחיל ברקע - זה ייקח כמה שניות.")


async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder until the AI conversation handler is added in a later stage."""
    await update.message.reply_text("שלח /start כדי לקשר את חשבון הגרמין שלך, או /sync כדי לסנכרן עכשיו.")


def build_application() -> Application:
    application = Application.builder().token(settings.telegram_bot_token).build()

    onboarding = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, do_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(onboarding)
    application.add_handler(CommandHandler("sync", sync_now))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))

    return application


def main() -> None:
    application = build_application()
    logger.info("Starting Telegram bot (long polling)")
    application.run_polling()


if __name__ == "__main__":
    main()
