"""
Minimal helper for pushing a Telegram message from synchronous code
(Celery tasks). app/bot.py runs its own async Application/Bot instance
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

logger = logging.getLogger(__name__)


async def _send(chat_id: int, text: str, markdown: bool) -> None:
    bot = Bot(token=settings.telegram_bot_token)
    if not markdown:
        await bot.send_message(chat_id=chat_id, text=text)
        return

    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    except BadRequest:
        # Unbalanced * or _ makes Telegram reject the whole message. Losing
        # the content over a formatting character would be far worse than
        # losing the bold, so fall back to plain text.
        logger.warning("Markdown rejected for chat %s, sending as plain text", chat_id)
        await bot.send_message(chat_id=chat_id, text=text)


def send_telegram_message(chat_id: int, text: str, markdown: bool = True) -> None:
    """
    Markdown defaults on: without it, formatted messages built elsewhere in
    the app arrived with their literal `*` characters visible, which is how
    this was originally shipped by mistake.
    """
    asyncio.run(_send(chat_id, text, markdown))
