"""
Background work: one sync per user at a time, and no task can hang forever.

The lock tests use the real Redis the workers use, because a lock is only
as good as the store holding it.
"""

from unittest.mock import patch

import pytest
from celery.exceptions import Retry

import app.tasks  # noqa: F401  - registers the tasks on the app
from app.celery_app import celery_app
from app.services.sync_lock import user_sync_lock
from app.tasks import (
    SYNC_HARD_LIMIT,
    SYNC_LOCK_SECONDS,
    BACKFILL_HARD_LIMIT,
    backfill_user_history_task,
    sync_one_user_task,
)

USER = 987654  # no database row needed: the real sync is replaced below


@pytest.fixture
def held_lock():
    lock = user_sync_lock(USER, 30)
    assert lock.acquire(blocking=False), "lock left over from another run?"
    yield lock
    lock.release()


@pytest.fixture
def fake_sync():
    with patch("app.tasks._sync_one_user") as fake:
        yield fake


def test_scheduled_sync_skips_when_one_is_already_running(held_lock, fake_sync):
    sync_one_user_task(USER)

    fake_sync.assert_not_called()


def test_manual_sync_waits_for_the_running_one_instead_of_skipping(held_lock, fake_sync):
    # The user pressed 🔄 and is expecting numbers back - retry shortly
    # rather than drop it.
    with pytest.raises(Retry):
        sync_one_user_task(USER, notify_on_success=True)

    fake_sync.assert_not_called()


def test_backfill_waits_for_the_quick_sync(held_lock):
    # Linking starts both together; skipping here would lose a month of history.
    with patch("app.tasks._backfill_user_history") as fake_backfill:
        with pytest.raises(Retry):
            backfill_user_history_task(USER)

    fake_backfill.assert_not_called()


def test_lock_is_released_after_sync(fake_sync):
    sync_one_user_task(USER)

    fake_sync.assert_called_once()
    again = user_sync_lock(USER, 30)
    assert again.acquire(blocking=False)
    again.release()


def test_lock_is_released_even_when_sync_fails(fake_sync):
    fake_sync.side_effect = RuntimeError("Garmin exploded")
    with pytest.raises(RuntimeError):
        sync_one_user_task(USER)

    again = user_sync_lock(USER, 30)
    assert again.acquire(blocking=False)
    again.release()


def test_lock_outlives_every_sync_time_limit():
    # A lock expiring while its sync still runs would let a second one in.
    assert SYNC_LOCK_SECONDS > max(SYNC_HARD_LIMIT, BACKFILL_HARD_LIMIT)


def _our_tasks():
    return {name: task for name, task in celery_app.tasks.items() if name.startswith("app.tasks.")}


def test_every_task_has_a_time_limit():
    # A Garmin or Claude call that never returns must not hold a worker forever.
    missing = [name for name, task in _our_tasks().items() if not task.time_limit or not task.soft_time_limit]
    assert missing == []


def test_only_idempotent_sync_tasks_are_acked_late():
    # Redelivering a sync is harmless (it upserts); redelivering a summary
    # sends the user the same message twice.
    acked_late = {name for name, task in _our_tasks().items() if task.acks_late}
    assert acked_late == {"app.tasks.sync_one_user_task", "app.tasks.backfill_user_history_task"}


def test_workers_take_one_task_at_a_time():
    assert celery_app.conf.worker_prefetch_multiplier == 1
