"""
Different users in parallel, the same user in order.

These drive PerUserUpdateProcessor directly with fake handlers that sleep,
so they measure the scheduling itself - no Telegram, no network.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from telegram import Chat, Message, Update, User

import app.bot_concurrency as bc
from app.bot_concurrency import PerUserUpdateProcessor


def _update(user_id: int, n: int = 1) -> Update:
    return Update(
        update_id=n,
        message=Message(
            message_id=n,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, first_name="u", is_bot=False),
            text="hi",
        ),
    )


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def notices():
    with patch.object(bc, "_send_busy_notice", new=AsyncMock()) as fake:
        yield fake


def _processor(max_pending: int = 5) -> PerUserUpdateProcessor:
    return PerUserUpdateProcessor(max_concurrent_updates=64, max_pending_per_user=max_pending)


def test_one_users_slow_question_does_not_block_another_user(notices):
    log = []

    async def handler(name, seconds):
        await asyncio.sleep(seconds)
        log.append(name)

    async def scenario():
        p = _processor()
        await asyncio.gather(
            p.process_update(_update(1), handler("slow coach answer", 0.5)),
            p.process_update(_update(2), handler("other user's button", 0.01)),
        )

    _run(scenario())

    # Before this change the bot handled one update at a time, so the
    # button would have waited the full half second behind the coach.
    assert log == ["other user's button", "slow coach answer"]


def test_same_users_updates_run_in_order_without_overlap(notices):
    log = []

    async def handler(n):
        log.append(f"start {n}")
        await asyncio.sleep(0.02)
        log.append(f"end {n}")

    async def scenario():
        p = _processor()
        await asyncio.gather(*(p.process_update(_update(1, n), handler(n)) for n in (1, 2, 3)))

    _run(scenario())

    assert log == ["start 1", "end 1", "start 2", "end 2", "start 3", "end 3"]


def test_waiting_behind_a_slow_answer_sends_one_notice(notices):
    async def scenario():
        p = _processor()
        with patch.object(bc, "BUSY_NOTICE_AFTER_SECONDS", 0.05):
            await asyncio.gather(
                p.process_update(_update(1, 1), asyncio.sleep(0.3)),
                p.process_update(_update(1, 2), asyncio.sleep(0)),
                p.process_update(_update(1, 3), asyncio.sleep(0)),
            )

    _run(scenario())

    # Two messages waited, but one "I'm still answering" is enough.
    assert notices.await_count == 1


def test_no_notice_behind_a_quick_handler(notices):
    async def scenario():
        p = _processor()
        await asyncio.gather(
            p.process_update(_update(1, 1), asyncio.sleep(0.01)),
            p.process_update(_update(1, 2), asyncio.sleep(0)),
        )

    _run(scenario())

    # A button finishes long before the notice threshold - telling the user
    # "still working" there would be noise.
    notices.assert_not_awaited()


def test_flood_from_one_user_is_capped(notices):
    done = []

    async def handler(n):
        await asyncio.sleep(0.05)
        done.append(n)

    async def scenario():
        p = _processor(max_pending=1)  # one running + one waiting
        await asyncio.gather(*(p.process_update(_update(1, n), handler(n)) for n in range(5)))

    _run(scenario())

    assert done == [0, 1]


def test_finished_users_leave_nothing_behind(notices):
    async def scenario():
        p = _processor()
        await asyncio.gather(*(p.process_update(_update(uid), asyncio.sleep(0)) for uid in range(20)))
        return p

    assert _run(scenario())._slots == {}


def test_bot_is_built_with_the_per_user_processor():
    from app.bot import build_application

    assert isinstance(build_application().update_processor, PerUserUpdateProcessor)
