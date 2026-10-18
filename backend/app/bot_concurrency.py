"""
How the bot handles many people at once.

python-telegram-bot's default is to process one update at a time, for
everyone. One person asking the coach a question (10-30 seconds) therefore
froze the bot for every other user - including someone who only tapped the
metrics button, which needs a few milliseconds.

Turning on plain concurrency fixes that and breaks three other things,
because two updates from the *same* person could then run at once:
  - both pass the quota check before either has been counted
  - two replies are written to the chat history out of order
  - form steps ("37", then "save") can be handled in the wrong order, and
    ConversationHandler itself is not safe under that

So the rule here is: different users in parallel, one user in order. Each
user gets a lock; an update waits for the previous update *from the same
person* and for nobody else.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Awaitable

from telegram import Update
from telegram.ext import BaseUpdateProcessor

from app.core.config import settings

logger = logging.getLogger(__name__)

# How long an update waits for the same user's previous one before the user
# is told it was received. Buttons finish well inside this, so the notice
# only appears behind something genuinely slow - in practice, the coach.
BUSY_NOTICE_AFTER_SECONDS = 1.0

BUSY_NOTICE = "⏳ אני עוד עונה על הבקשה הקודמת שלך - אטפל בזו מיד אחריה."

# The coach's own threads. Separate from the default pool asyncio.to_thread
# uses, so a burst of coach questions cannot starve a Garmin login waiting
# for a thread - and so capacity is a number in config.py rather than
# whatever the server's core count happens to produce.
coach_executor = ThreadPoolExecutor(
    max_workers=settings.coach_max_concurrent,
    thread_name_prefix="coach",
)


@dataclass
class _UserSlot:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Updates holding or waiting for this lock. The slot is dropped when it
    # reaches zero, so the dict holds active users only, not everyone ever.
    in_flight: int = 0
    # One notice per busy stretch, not one per queued message.
    notified: bool = False


class PerUserUpdateProcessor(BaseUpdateProcessor):
    """Concurrent across users, sequential within each user."""

    def __init__(self, max_concurrent_updates: int, max_pending_per_user: int):
        super().__init__(max_concurrent_updates)
        self._max_pending = max_pending_per_user
        self._slots: dict[int, _UserSlot] = {}

    async def do_process_update(self, update: object, coroutine: Awaitable[Any]) -> None:
        user_id = _user_id(update)
        if user_id is None:
            # No person behind it (e.g. a channel post) - nothing to order.
            await coroutine
            return

        slot = self._slots.setdefault(user_id, _UserSlot())
        if slot.in_flight > self._max_pending:
            logger.warning("Dropping update from user %s: %s already queued", user_id, slot.in_flight)
            _close(coroutine)
            return

        slot.in_flight += 1
        try:
            await self._acquire(slot, update)
            try:
                await coroutine
            finally:
                slot.lock.release()
        finally:
            slot.in_flight -= 1
            if slot.in_flight == 0:
                self._slots.pop(user_id, None)

    async def _acquire(self, slot: _UserSlot, update: object) -> None:
        if not slot.lock.locked():
            await slot.lock.acquire()
            slot.notified = False  # a fresh busy stretch starts with this update
            return

        # Someone - this same user - is ahead. Wait quietly for a moment,
        # since a button handler finishes almost at once; only if the wait
        # is real does the user hear that their message was received.
        # One acquire attempt, waited on twice: briefly, then for as long as
        # it takes. Starting a second acquire after a timeout would leave the
        # first one still pending - and taking the lock behind our back.
        acquiring = asyncio.ensure_future(slot.lock.acquire())
        done, _ = await asyncio.wait({acquiring}, timeout=BUSY_NOTICE_AFTER_SECONDS)
        if not done and not slot.notified:
            slot.notified = True
            await _send_busy_notice(update)
        await acquiring

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        coach_executor.shutdown(wait=False, cancel_futures=True)


def _user_id(update: object):
    if isinstance(update, Update) and update.effective_user is not None:
        return update.effective_user.id
    return None


def _close(coroutine) -> None:
    # A coroutine that is never awaited logs a warning; close it explicitly.
    close = getattr(coroutine, "close", None)
    if close is not None:
        close()


async def _send_busy_notice(update: object) -> None:
    try:
        await update.effective_chat.send_message(BUSY_NOTICE)
    except Exception:
        # A notice that fails to send must never cost the real reply.
        logger.debug("busy notice failed", exc_info=True)


def build_update_processor() -> PerUserUpdateProcessor:
    return PerUserUpdateProcessor(
        max_concurrent_updates=settings.bot_max_concurrent_updates,
        max_pending_per_user=settings.bot_max_pending_per_user,
    )
