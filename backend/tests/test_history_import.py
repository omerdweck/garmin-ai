"""
The one-time import of a user's past year.

Garmin is replaced by two small fakes that record every call, because the
two things that matter most here are about *calls*: that a day we already
have never costs a request, and that stopping half-way (throttling, a
crash) loses nothing and repeats nothing.
"""

from datetime import date, datetime, timedelta

import pytest
from garminconnect.exceptions import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
from sqlmodel import select

from app.models.activity import Activity
from app.models.daily_metric import DailyMetric
from app.models.history_import import STATUS_DONE, STATUS_PAUSED, STATUS_RUNNING
from app.services import history_import as hi

TODAY = date(2026, 10, 25)


class FakeGarmin:
    def __init__(self, activities=(), fail_on_day=None, error=None):
        self.day_calls: list[str] = []
        self.page_calls: list[int] = []
        self.activities = list(activities)  # newest first, like Garmin
        self.fail_on_day = fail_on_day
        self.error = error

    def list_activities(self, _session, start, limit):
        self.page_calls.append(start)
        return self.activities[start:start + limit]

    def fetch_day(self, _session, day: str, _pause):
        if day == self.fail_on_day:
            raise self.error
        self.day_calls.append(day)
        return {"summary": {"restingHeartRate": 50}, "sleep": {}, "hrv": {}, "max_metrics": []}


def activity(garmin_id: int, day: date) -> dict:
    return {
        "activityId": garmin_id,
        "startTimeLocal": f"{day.isoformat()} 07:00:00",
        "activityType": {"typeKey": "running"},
        "duration": 1800.0,
        "distance": 5000.0,
    }


def run(session, record, garmin, chunk_days=5):
    return hi.run_chunk(
        session, record, None, chunk_days=chunk_days, pause_seconds=0,
        list_activities=garmin.list_activities, fetch_day=garmin.fetch_day,
    )


# --- the night window --------------------------------------------------------


@pytest.mark.parametrize("hour, inside", [(22, False), (23, True), (0, True), (5, True), (6, False), (14, False)])
def test_night_window_wraps_midnight(hour, inside):
    assert hi.in_night_window(hour, 23, 6) is inside


# --- the range ----------------------------------------------------------------


def test_range_starts_yesterday_and_spans_the_configured_days(session, make_user):
    user = make_user()
    record = hi.ensure_import(session, user.id, TODAY, days=407)
    assert record.newest_date == TODAY - timedelta(days=1)
    assert (record.newest_date - record.oldest_date).days + 1 == 407
    assert record.next_date == record.newest_date


def test_ensure_import_does_not_reset_an_existing_import(session, make_user):
    user = make_user()
    record = hi.ensure_import(session, user.id, TODAY, days=10)
    run(session, record, FakeGarmin(), chunk_days=4)
    again = hi.ensure_import(session, user.id, TODAY + timedelta(days=3), days=10)
    assert again.id == record.id
    assert again.days_done == 4


# --- days: newest first, resumable, no wasted calls ---------------------------


def test_days_are_fetched_newest_first_and_resume_where_they_stopped(session, make_user):
    user = make_user()
    record = hi.ensure_import(session, user.id, TODAY, days=10)
    garmin = FakeGarmin()

    run(session, record, garmin, chunk_days=3)
    assert garmin.day_calls == ["2026-10-24", "2026-10-23", "2026-10-22"]
    assert record.status == STATUS_RUNNING

    run(session, record, garmin, chunk_days=3)
    assert garmin.day_calls[3:] == ["2026-10-21", "2026-10-20", "2026-10-19"]
    assert record.days_done == 6


def test_a_day_already_stored_costs_no_garmin_call(session, make_user):
    user = make_user()
    session.add(DailyMetric(user_id=user.id, date=TODAY - timedelta(days=2), raw_json={"summary": {}}))
    session.commit()
    record = hi.ensure_import(session, user.id, TODAY, days=4)
    garmin = FakeGarmin()

    run(session, record, garmin, chunk_days=10)

    assert "2026-10-23" not in garmin.day_calls
    assert len(garmin.day_calls) == 3
    assert record.days_done == 4
    assert record.days_fetched == 3


def test_finishing_marks_done_at_100_percent(session, make_user):
    user = make_user()
    record = hi.ensure_import(session, user.id, TODAY, days=3)
    run(session, record, FakeGarmin(), chunk_days=10)
    assert record.status == STATUS_DONE
    assert record.percent == 100
    assert record.finished_at is not None
    stored = session.exec(select(DailyMetric).where(DailyMetric.user_id == user.id)).all()
    assert len(stored) == 3


def test_a_finished_import_makes_no_more_calls(session, make_user):
    user = make_user()
    record = hi.ensure_import(session, user.id, TODAY, days=2)
    run(session, record, FakeGarmin(), chunk_days=10)
    garmin = FakeGarmin()
    assert run(session, record, garmin) == 0
    assert garmin.day_calls == [] and garmin.page_calls == []


def test_percent_is_never_100_before_done(session, make_user):
    user = make_user()
    record = hi.ensure_import(session, user.id, TODAY, days=200)
    record.days_done = 199
    assert record.percent == 99


# --- Garmin refusing ----------------------------------------------------------


def test_throttling_keeps_every_finished_day_and_resumes_at_the_failed_one(session, make_user):
    user = make_user()
    record = hi.ensure_import(session, user.id, TODAY, days=10)
    garmin = FakeGarmin(fail_on_day="2026-10-22", error=GarminConnectTooManyRequestsError("429"))

    with pytest.raises(hi.ImportThrottled):
        run(session, record, garmin, chunk_days=10)

    session.refresh(record)
    assert record.days_done == 2
    assert record.next_date == date(2026, 10, 22)
    assert record.last_error.startswith("throttled")

    garmin.fail_on_day = None
    run(session, record, garmin, chunk_days=1)
    assert garmin.day_calls[-1] == "2026-10-22"


def test_a_rejected_login_pauses_the_import(session, make_user):
    user = make_user()
    record = hi.ensure_import(session, user.id, TODAY, days=10)
    garmin = FakeGarmin(fail_on_day="2026-10-24", error=GarminConnectAuthenticationError("401"))
    run(session, record, garmin)
    session.refresh(record)
    assert record.status == STATUS_PAUSED


# --- activities ---------------------------------------------------------------


def test_activities_inside_the_range_are_imported_once(session, make_user):
    user = make_user()
    record = hi.ensure_import(session, user.id, TODAY, days=30)
    garmin = FakeGarmin(activities=[
        activity(3, TODAY - timedelta(days=1)),
        activity(2, TODAY - timedelta(days=20)),
        activity(1, TODAY - timedelta(days=100)),  # older than the range
    ])

    run(session, record, garmin, chunk_days=1)
    run(session, record, garmin, chunk_days=1)

    stored = session.exec(select(Activity.garmin_activity_id).where(Activity.user_id == user.id)).all()
    assert sorted(stored) == [2, 3]
    assert garmin.page_calls == [0]  # one page, and not asked again on the second run
    assert record.activities_done and record.activities_imported == 2


def test_activity_paging_stops_once_a_page_reaches_past_the_range(session, make_user):
    user = make_user()
    record = hi.ensure_import(session, user.id, TODAY, days=30)
    recent = [activity(1000 + i, TODAY - timedelta(days=1)) for i in range(hi.ACTIVITY_PAGE_SIZE)]
    old = [activity(i, TODAY - timedelta(days=200)) for i in range(hi.ACTIVITY_PAGE_SIZE)]
    garmin = FakeGarmin(activities=recent + recent[:0] + old + old)

    run(session, record, garmin, chunk_days=1)

    assert garmin.page_calls == [0, hi.ACTIVITY_PAGE_SIZE]


# --- who it runs for ----------------------------------------------------------


def test_scope_admin_means_only_the_owner(session, make_user, monkeypatch):
    owner = make_user()
    make_user()
    assert hi.eligible_user_ids(session, "admin", owner.telegram_chat_id) == [owner.id]
    assert hi.eligible_user_ids(session, "admin", None) == []
    assert hi.eligible_user_ids(session, "off", owner.telegram_chat_id) == []
    assert len(hi.eligible_user_ids(session, "all", None)) == 2


def test_unlinked_users_are_not_eligible(session, make_user):
    make_user(linked=False)
    assert hi.eligible_user_ids(session, "all", None) == []


def test_users_take_turns(session, make_user):
    a, b = make_user(), make_user()
    first = hi.ensure_import(session, a.id, TODAY, days=10)
    hi.ensure_import(session, b.id, TODAY, days=10)
    run(session, first, FakeGarmin(), chunk_days=1)  # touches a's row last
    assert hi.next_user_to_import(session, [a.id, b.id]) == b.id


# --- the Mini App's progress bar ----------------------------------------------


def test_history_status_endpoint(session, make_user):
    from fastapi.testclient import TestClient

    from app.core.security import create_miniapp_token
    from app.db.session import get_session
    from app.webapp import app

    user = make_user()
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {create_miniapp_token(user.id)}"}
        assert client.get("/api/history-status", headers=headers).json() == {
            "status": "none", "percent": 0, "loaded_from": None,
        }

        record = hi.ensure_import(session, user.id, TODAY, days=10)
        run(session, record, FakeGarmin(), chunk_days=4)
        body = client.get("/api/history-status", headers=headers).json()
        assert body == {"status": "running", "percent": 40, "loaded_from": "2026-10-21"}
    finally:
        app.dependency_overrides.clear()


# --- the task: one chunk at a time, never next to a sync ----------------------


def test_only_one_chunk_runs_at_a_time(make_user, monkeypatch):
    from unittest.mock import MagicMock

    from app import tasks
    from app.services.sync_lock import named_lock, release

    fake = MagicMock()
    monkeypatch.setattr(tasks, "_import_history_chunk", fake)
    held = named_lock("history-import", 60)
    assert held.acquire(blocking=False)
    try:
        tasks.import_history_chunk_task(make_user().id)
    finally:
        release(held)
    fake.assert_not_called()


def test_a_chunk_waits_for_the_users_sync(make_user, monkeypatch):
    from unittest.mock import MagicMock

    from app import tasks
    from app.services.sync_lock import release, user_sync_lock

    user = make_user()
    fake = MagicMock()
    monkeypatch.setattr(tasks, "_import_history_chunk", fake)
    held = user_sync_lock(user.id, 60)
    assert held.acquire(blocking=False)
    try:
        tasks.import_history_chunk_task(user.id)
    finally:
        release(held)
    fake.assert_not_called()


def test_dispatch_does_nothing_by_day(monkeypatch):
    from unittest.mock import MagicMock

    from app import tasks

    fake = MagicMock()
    monkeypatch.setattr(tasks.import_history_chunk_task, "delay", fake)
    monkeypatch.setattr(tasks.clock, "now_local", lambda: datetime(2026, 10, 25, 14, 0))
    tasks.dispatch_history_import()
    fake.assert_not_called()
