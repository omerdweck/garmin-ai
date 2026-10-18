"""
The scheduled sync runs twice a day. A watch that stopped uploading must
produce ONE warning per episode, not one per run - fourteen identical
messages for one forgotten week is how a bot gets muted.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlmodel import select

from app.models.garmin_account import GarminAccount
from app.services.metrics_view import WATCH_STALE_HOURS
from app.tasks import _warn_if_watch_stale


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def sent():
    """Captures outgoing Telegram messages instead of sending them."""
    with patch("app.tasks.send_telegram_message") as fake_send:
        yield fake_send


def _set_last_upload(session, user, when):
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).one()
    account.watch_last_upload_at = when
    session.add(account)
    session.commit()


def test_recent_upload_sends_nothing(session, make_user, sent):
    me = make_user()
    _set_last_upload(session, me, _now() - timedelta(hours=1))

    _warn_if_watch_stale(session, me)

    sent.assert_not_called()


def test_stale_watch_warns_once(session, make_user, sent):
    me = make_user()
    _set_last_upload(session, me, _now() - timedelta(hours=WATCH_STALE_HOURS + 1))

    _warn_if_watch_stale(session, me)
    _warn_if_watch_stale(session, me)  # the next scheduled run
    _warn_if_watch_stale(session, me)

    assert sent.call_count == 1


def test_new_episode_after_watch_syncs_again_warns_again(session, make_user, sent):
    me = make_user()
    # We warned three days ago. Since then the watch uploaded (so that episode
    # ended) and has now been silent long enough to be stale again.
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == me.id)).one()
    account.watch_stale_notified_at = _now() - timedelta(days=3)
    account.watch_last_upload_at = _now() - timedelta(hours=WATCH_STALE_HOURS + 1)
    session.add(account)
    session.commit()

    _warn_if_watch_stale(session, me)

    assert sent.call_count == 1


def test_no_upload_time_recorded_sends_nothing(session, make_user, sent):
    me = make_user()  # linked, but we have never seen an upload time

    _warn_if_watch_stale(session, me)

    sent.assert_not_called()
