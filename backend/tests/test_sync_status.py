"""
The page's "updated N minutes ago" line and its "sync now" button.

Redis is a fake here: what matters is which keys the endpoints look at and
which they set, and that a button pressed freely can't turn into a stream
of Garmin requests.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.security import create_miniapp_token
from app.db.session import get_session
from app.models.garmin_account import GarminAccount
from app.services import app_sync
from app.webapp import app


class FakeRedis:
    def __init__(self):
        self.values = {}

    def exists(self, key):
        return key in self.values

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True


@pytest.fixture
def fake(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(app_sync, "_redis", lambda: fake)
    return fake


@pytest.fixture
def sent(monkeypatch):
    from app.celery_app import celery_app

    sent = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, args: sent.append((name, args)))
    return sent


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def headers(user):
    return {"Authorization": f"Bearer {create_miniapp_token(user.id)}"}


def set_last_sync(session, user, when):
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).one()
    account.last_sync_at = when
    session.add(account)
    session.commit()


def test_status_reports_the_last_successful_sync(client, session, make_user, fake):
    user = make_user()
    assert client.get("/api/sync-status", headers=headers(user)).json() == {
        "last_synced_at": None, "syncing": False, "linked": True,
    }
    when = datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)
    set_last_sync(session, user, when)
    body = client.get("/api/sync-status", headers=headers(user)).json()
    assert datetime.fromisoformat(body["last_synced_at"]) == when


def test_status_sees_a_running_sync_by_its_lock(client, make_user, fake):
    user = make_user()
    fake.values[app_sync.LOCK_KEY.format(user.id)] = "token"
    assert client.get("/api/sync-status", headers=headers(user)).json()["syncing"] is True


def test_status_needs_a_token(client):
    assert client.get("/api/sync-status").status_code == 401
    assert client.post("/api/sync").status_code == 401


def test_sync_now_queues_the_bots_sync_task_once(client, session, make_user, fake, sent):
    user = make_user()
    set_last_sync(session, user, datetime.now(timezone.utc) - timedelta(hours=3))
    assert client.post("/api/sync", headers=headers(user)).json() == {"status": "started"}
    # Queued but not yet picked up by a worker: already "syncing", and a
    # second tap queues nothing.
    assert client.get("/api/sync-status", headers=headers(user)).json()["syncing"] is True
    assert client.post("/api/sync", headers=headers(user)).json() == {"status": "syncing"}
    assert sent == [("app.tasks.sync_one_user_task", [user.id])]


def test_request_counts_as_done_once_a_newer_sync_finished(client, session, make_user, fake, sent):
    user = make_user()
    client.post("/api/sync", headers=headers(user))
    set_last_sync(session, user, datetime.now(timezone.utc) + timedelta(seconds=5))
    assert client.get("/api/sync-status", headers=headers(user)).json()["syncing"] is False


@pytest.mark.parametrize("case, expected", [
    ("throttled", "busy"),
    ("locked", "syncing"),
    ("fresh", "fresh"),
    ("disconnected", "unlinked"),
])
def test_sync_now_refuses_when_it_would_not_help(client, session, make_user, fake, sent, case, expected):
    user = make_user()
    set_last_sync(session, user, datetime.now(timezone.utc) - timedelta(hours=3))
    if case == "throttled":
        fake.values[app_sync.THROTTLE_KEY] = "1"
    elif case == "locked":
        fake.values[app_sync.LOCK_KEY.format(user.id)] = "token"
    elif case == "fresh":
        set_last_sync(session, user, datetime.now(timezone.utc) - timedelta(minutes=1))
    elif case == "disconnected":
        account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).one()
        account.disconnected_at = datetime.now(timezone.utc)
        session.add(account)
        session.commit()
    assert client.post("/api/sync", headers=headers(user)).json() == {"status": expected}
    assert sent == []


def test_user_without_garmin(client, make_user, fake, sent):
    user = make_user(linked=False)
    assert client.get("/api/sync-status", headers=headers(user)).json()["linked"] is False
    assert client.post("/api/sync", headers=headers(user)).json() == {"status": "unlinked"}
    assert sent == []


def test_keys_match_the_workers(fake):
    # Repeated rather than imported (see app_sync's docstring) - so checked here.
    from app.services import garmin_throttle, sync_lock

    assert app_sync.THROTTLE_KEY == garmin_throttle.KEY
    assert sync_lock.user_sync_lock(7, 10).name == app_sync.LOCK_KEY.format(7)
