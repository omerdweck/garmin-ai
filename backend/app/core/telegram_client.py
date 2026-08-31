"""
Minimal helper for pushing a Telegram message from synchronous code
(Celery tasks). app/bot.py runs its own async Application/Bot instance
to *handle* incoming messages, which doesn't help here - a background
task has no incoming update to reply to, it needs to proactively send
one. asyncio.run() is fine for this: each call is a single short-lived
request, not something worth keeping a persistent event loop open for.
"""

import asyncio

from telegram import Bot

from app.core.config import settings


def send_telegram_message(chat_id: int, text: str) -> None:
    asyncio.run(Bot(token=settings.telegram_bot_token).send_message(chat_id=chat_id, text=text))
