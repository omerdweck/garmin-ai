"""
The bell: every unprompted message lands on the notifications screen once,
nothing else does, and each user sees and marks only their own.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

import app.services.coach_events as ce
from app.core.security import create_miniapp_token
from app.db.session import get_session
from app.models.chat_message import ChatMessage
from app.models.coach_event import KIND_ANOMALY, KIND_POST_WORKOUT, STATUS_SENT, STATUS_SKIPPED, CoachEvent
from app.models.metric_alert import MetricAlert
from app.models.notification import Notification
from app.services import notifications
from app.webapp import app


@pytest.fixture
def sent():
    with patch.object(ce, "send_telegram_message") as fake:
        yield fake


@pytest.fixture
def composers(monkeypatch):
    monkeypatch.setitem(ce.COMPOSERS, KIND_POST_WORKOUT, ce.Composer(lambda s, u, e: "*כל הכבוד!*", uses_ai=False))
    monkeypatch.setitem(ce.COMPOSERS, KIND_ANOMALY, ce.Composer(lambda s, u, e: "שים לב ל-HRV", uses_ai=False))


def _rows(session, user=None):
    query = select(Notification).order_by(Notification.id)
    if user is not None:
        query = query.where(Notification.user_id == user.id)
    return session.exec(query).all()


# --- recording on delivery ---------------------------------------------------


def test_a_delivered_post_workout_message_is_recorded_with_its_workout(session, make_user, sent, composers):
    me = make_user()
    event = ce.record_event(session, me.id, KIND_POST_WORKOUT, "post_workout:42", {"activity_id": 42})

    assert ce.deliver(session, event.id) == "sent"

    [row] = _rows(session, me)
    assert (row.kind, row.text, row.read_at) == (KIND_POST_WORKOUT, "*כל הכבוד!*", None)
    assert row.link == {"type": "activity", "id": 42}
    assert row.source_key == f"coach_event:{event.id}"


def test_an_anomaly_links_to_the_card_of_the_metric_that_drifted(session, make_user, sent, composers):
    me = make_user()
    alert = MetricAlert(user_id=me.id, rule="hrv_drop", started_on=date.today(), last_seen_on=date.today())
    session.add(alert)
    session.commit()
    event = ce.record_event(session, me.id, KIND_ANOMALY, "anomaly:1", {"alert_ids": [alert.id]})

    ce.deliver(session, event.id)

    [row] = _rows(session, me)
    assert row.link == {"type": "tab", "tab": "health", "focus": "metric-avg_hrv"}


def test_sleep_anomaly_points_at_the_sleep_card(session, make_user):
    me = make_user()
    alert = MetricAlert(user_id=me.id, rule="sleep_poor", started_on=date.today(), last_seen_on=date.today())
    session.add(alert)
    session.commit()
    event = CoachEvent(user_id=me.id, kind=KIND_ANOMALY, key="a", payload={"alert_ids": [alert.id]})

    assert notifications.link_for_event(session, event) == {"type": "tab", "tab": "health", "focus": "metric-sleep"}


def test_a_skipped_event_records_nothing(session, make_user, sent, composers):
    me = make_user(notify_post_workout=False)
    event = ce.record_event(session, me.id, KIND_POST_WORKOUT, "post_workout:1", {"activity_id": 1})

    assert ce.deliver(session, event.id) == "skipped:turned_off"

    assert _rows(session) == []


def test_a_failed_send_records_nothing(session, make_user, sent, composers):
    me = make_user()
    event = ce.record_event(session, me.id, KIND_POST_WORKOUT, "post_workout:1", {"activity_id": 1})
    sent.side_effect = RuntimeError("telegram down")

    with pytest.raises(RuntimeError):
        ce.deliver(session, event.id)

    session.rollback()
    assert _rows(session) == []


def test_recording_the_same_message_twice_is_a_no_op(session, make_user):
    me = make_user()

    notifications.record_safely(session, me.id, "weekly_report", "סיכום", "weekly_report:2026-10-24")
    notifications.record_safely(session, me.id, "weekly_report", "סיכום שוב", "weekly_report:2026-10-24")

    [row] = _rows(session, me)
    assert row.text == "סיכום"


def test_weekly_summary_is_recorded_when_sent(session, make_user):
    me = make_user()
    with patch("app.tasks.generate_weekly_summary", return_value="📅 *סיכום שבועי*"), \
         patch("app.tasks.send_telegram_message"):
        from app.tasks import send_weekly_summary_task

        send_weekly_summary_task(me.id)

    [row] = _rows(session, me)
    assert (row.kind, row.text) == ("weekly_report", "📅 *סיכום שבועי*")
    assert row.link == {"type": "tab", "tab": "workouts"}


def test_an_empty_weekly_summary_records_nothing(session, make_user):
    me = make_user()
    with patch("app.tasks.generate_weekly_summary", return_value=None), \
         patch("app.tasks.send_telegram_message"):
        from app.tasks import send_weekly_summary_task

        send_weekly_summary_task(me.id)

    assert _rows(session) == []


# --- the backfill --------------------------------------------------------------


def _old_sent_event(session, user, key, sent_at, text, kind=KIND_POST_WORKOUT, payload=None):
    event = CoachEvent(
        user_id=user.id, kind=kind, key=key, payload=payload or {"activity_id": 7},
        status=STATUS_SENT, sent_at=sent_at,
    )
    session.add(event)
    if text is not None:
        session.add(ChatMessage(user_id=user.id, role="assistant", content=text,
                                created_at=sent_at - timedelta(milliseconds=30)))
    session.commit()
    return event


def test_backfill_files_old_messages_as_read_at_their_time(session, make_user):
    me = make_user()
    when = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
    # Noise either side: the user's own message, and a coach reply hours later.
    session.add(ChatMessage(user_id=me.id, role="user", content="היי", created_at=when - timedelta(seconds=5)))
    session.add(ChatMessage(user_id=me.id, role="assistant", content="תשובה", created_at=when + timedelta(hours=2)))
    event = _old_sent_event(session, me, "post_workout:7", when, "ריצה יפה!")

    assert notifications.backfill_from_coach_events(session, me.id) == 1

    [row] = _rows(session, me)
    assert row.text == "ריצה יפה!"
    assert row.source_key == f"coach_event:{event.id}"
    assert row.link == {"type": "activity", "id": 7}
    assert row.created_at == when.replace(tzinfo=None)
    assert row.read_at is not None


def test_backfill_is_idempotent_and_skips_what_deliver_already_filed(session, make_user):
    me = make_user()
    when = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
    first = _old_sent_event(session, me, "post_workout:1", when, "ראשון")
    _old_sent_event(session, me, "post_workout:2", when + timedelta(days=1), "שני")
    notifications.record_safely(session, me.id, KIND_POST_WORKOUT, "ראשון", f"coach_event:{first.id}")

    assert notifications.backfill_from_coach_events(session, me.id) == 1
    assert notifications.backfill_from_coach_events(session, me.id) == 0

    assert sorted(r.text for r in _rows(session, me)) == ["ראשון", "שני"]


def test_backfill_ignores_skipped_events_and_events_without_text(session, make_user):
    me, other = make_user(), make_user()
    when = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
    skipped = CoachEvent(user_id=me.id, kind=KIND_POST_WORKOUT, key="post_workout:3", status=STATUS_SKIPPED)
    session.add(skipped)
    session.commit()
    _old_sent_event(session, me, "post_workout:4", when, None)
    # Someone else's message at the very same moment is not this user's text.
    session.add(ChatMessage(user_id=other.id, role="assistant", content="של מישהו אחר", created_at=when))
    session.commit()

    assert notifications.backfill_from_coach_events(session, me.id) == 0
    assert _rows(session) == []


# --- the API -----------------------------------------------------------------


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    # No Redis or Celery in these tests; the backfill trigger has its own test.
    with patch.object(notifications, "request_backfill", return_value=False):
        yield TestClient(app)
    app.dependency_overrides.clear()


def _headers(user):
    return {"Authorization": f"Bearer {create_miniapp_token(user.id)}"}


def _fill(session, user, count):
    for i in range(count):
        notifications.record(session, user.id, "post_workout", f"הודעה {i}", f"k:{i}", {"type": "activity", "id": i})
    session.commit()


def test_list_pages_newest_first(client, session, make_user):
    me = make_user()
    _fill(session, me, 25)

    first = client.get("/api/notifications", headers=_headers(me)).json()
    assert len(first["items"]) == 20 and first["more"] is True and first["unread"] == 25
    assert first["items"][0]["text"] == "הודעה 24"
    assert first["items"][0]["created_at"].endswith("+00:00")

    before = first["items"][-1]["id"]
    second = client.get(f"/api/notifications?before={before}", headers=_headers(me)).json()
    assert [i["text"] for i in second["items"]] == [f"הודעה {i}" for i in range(4, -1, -1)]
    assert second["more"] is False


def test_unread_count_and_marking_read(client, session, make_user):
    me = make_user()
    _fill(session, me, 3)
    ids = [r.id for r in _rows(session, me)]

    assert client.get("/api/notifications/unread", headers=_headers(me)).json() == {"unread": 3}

    res = client.post("/api/notifications/read", json={"ids": [ids[0]]}, headers=_headers(me))
    assert res.json() == {"unread": 2}
    items = client.get("/api/notifications", headers=_headers(me)).json()["items"]
    assert [i["read"] for i in items] == [False, False, True]

    assert client.post("/api/notifications/read", json={"all": True}, headers=_headers(me)).json() == {"unread": 0}


def test_users_see_and_mark_only_their_own(client, session, make_user):
    me, other = make_user(), make_user()
    _fill(session, other, 2)
    theirs = [r.id for r in _rows(session, other)]

    assert client.get("/api/notifications", headers=_headers(me)).json()["items"] == []
    assert client.post("/api/notifications/read", json={"ids": theirs}, headers=_headers(me)).json() == {"unread": 0}
    client.post("/api/notifications/read", json={"all": True}, headers=_headers(me))

    session.expire_all()
    assert all(r.read_at is None for r in _rows(session, other))


def test_notifications_need_a_token(client):
    assert client.get("/api/notifications").status_code == 401
    assert client.get("/api/notifications/unread").status_code == 401
    assert client.post("/api/notifications/read", json={"all": True}).status_code == 401


def test_the_backfill_is_queued_once(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.keys = set()

        def set(self, key, value, nx, ex):
            if key in self.keys:
                return None
            self.keys.add(key)
            return True

    fake = FakeRedis()
    monkeypatch.setattr(notifications, "_redis", lambda: fake)
    with patch("app.celery_app.celery_app.send_task") as send:
        assert notifications.request_backfill(5) is True
        assert notifications.request_backfill(5) is False
    send.assert_called_once_with("app.tasks.backfill_notifications_task", args=[5])
