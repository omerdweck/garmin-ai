"""
Minimal helper for pushing a Telegram message from synchronous code
(Celery tasks). app/bot/ runs its own async Application/Bot instance
to *handle* incoming messages, which doesn't help here - a background
task has no incoming update to reply to, it needs to proactively send
one. asyncio.run() is fine for this: each call is a single short-lived
request, not something worth keeping a persistent event loop open for.
"""

import asyncio
import logging

from telegram import Bot
from telegram.error import BadRequest

from app.core.config import settings

# Same reason as in app/bot/: python-telegram-bot talks to
# api.telegram.org/bot<TOKEN>/... over httpx, and httpx logs whole URLs at
# INFO - so the bot token lands in the worker and beat logs too. Set here,
# at import of the module that actually makes those calls, so every
# entrypoint that can send a message is covered.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def _send(chat_id: int, text: str, markdown: bool, reply_markup=None) -> None:
    bot = Bot(token=settings.telegram_bot_token)
    if not markdown:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        return

    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=reply_markup)
    except BadRequest:
        # Unbalanced * or _ makes Telegram reject the whole message. Losing
        # the content over a formatting character would be far worse than
        # losing the bold, so fall back to plain text.
        logger.warning("Markdown rejected for chat %s, sending as plain text", chat_id)
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


def send_telegram_message(chat_id: int, text: str, markdown: bool = True, reply_markup=None) -> None:
    """
    Markdown defaults on: without it, formatted messages built elsewhere in
    the app arrived with their literal `*` characters visible, which is how
    this was originally shipped by mistake.

    `reply_markup` is an InlineKeyboardMarkup, for messages that carry buttons.
    """
    asyncio.run(_send(chat_id, text, markdown, reply_markup))


async def _send_document(chat_id: int, content: bytes, filename: str, caption: str) -> None:
    bot = Bot(token=settings.telegram_bot_token)
    await bot.send_document(chat_id=chat_id, document=content, filename=filename, caption=caption)


def send_telegram_document(chat_id: int, content: bytes, filename: str, caption: str = "") -> None:
    """A file as a Telegram attachment - used for workout files (.fit, .zwo, .erg, .mrc)."""
    asyncio.run(_send_document(chat_id, content, filename, caption))
