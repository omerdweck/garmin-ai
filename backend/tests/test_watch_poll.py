"""
The 30-minute "has the watch uploaded?" check: cheap, only for people who
want the post-workout message, and silent when Garmin is throttling us.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlmodel import select

import app.tasks as tasks
from app.celery_app import celery_app
from app.core.garmin_client import GarminRateLimitError
from app.models.garmin_account import GarminAccount
from app.services import garmin_throttle


@pytest.fixture(autouse=True)
def not_throttled():
    garmin_throttle._redis().delete(garmin_throttle.KEY)
    yield
    garmin_throttle._redis().delete(garmin_throttle.KEY)


@pytest.fixture
def enqueued():
    with patch.object(tasks.check_watch_upload_task, "apply_async") as checks, \
         patch.object(tasks.sync_one_user_task, "delay") as syncs:
        yield checks, syncs


def _checked_users(checks):
    return sorted(call.kwargs["args"][0] for call in checks.call_args_list)


def test_only_opted_in_connected_users_are_checked(session, make_user, enqueued):
    checks, _ = enqueued
    wants_it = make_user()
    make_user(notify_post_workout=False)
    disconnected = make_user()
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == disconnected.id)).one()
    account.disconnected_at = datetime.now(timezone.utc)
    session.add(account)
    session.commit()

    tasks.poll_watch_uploads()

    assert _checked_users(checks) == [wants_it.id]


def test_nothing_is_checked_while_garmin_throttles(make_user, enqueued):
    checks, _ = enqueued
    make_user()
    garmin_throttle.mark_throttled()

    tasks.poll_watch_uploads()

    checks.assert_not_called()


def _set_upload(session, user, when):
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).one()
    account.watch_last_upload_at = when
    session.add(account)
    session.commit()


@pytest.fixture
def garmin_says():
    def _setup(latest=None, error=None):
        return patch.multiple(
            tasks,
            resume_garmin_session=lambda token: object(),
            decrypt=lambda token: token,
            get_watch_last_upload=(lambda g: (_ for _ in ()).throw(error)) if error else (lambda g: latest),
        )
    return _setup


def test_a_new_upload_triggers_a_sync(session, make_user, enqueued, garmin_says):
    _, syncs = enqueued
    me = make_user()
    then = datetime.now(timezone.utc) - timedelta(hours=3)
    _set_upload(session, me, then)

    with garmin_says(latest=then + timedelta(hours=2)):
        tasks.check_watch_upload_task(me.id)

    syncs.assert_called_once_with(me.id)


def test_no_new_upload_means_no_sync(session, make_user, enqueued, garmin_says):
    _, syncs = enqueued
    me = make_user()
    then = datetime.now(timezone.utc) - timedelta(hours=3)
    _set_upload(session, me, then)

    with garmin_says(latest=then):
        tasks.check_watch_upload_task(me.id)

    syncs.assert_not_called()


def test_rate_limit_pauses_everyone(make_user, enqueued, garmin_says):
    me = make_user()
    with garmin_says(error=GarminRateLimitError("429")):
        tasks.check_watch_upload_task(me.id)

    assert garmin_throttle.is_throttled()


def test_poll_runs_every_half_hour_during_the_day():
    entry = celery_app.conf.beat_schedule["poll-watch-uploads"]
    assert entry["task"] == "app.tasks.poll_watch_uploads"
    assert entry["schedule"].minute == {10, 40}
    assert entry["schedule"].hour == set(range(6, 22))
