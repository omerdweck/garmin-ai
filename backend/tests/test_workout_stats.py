"""
The workouts tab's numbers: periods, totals, hours by sport, and
Fitness/Fatigue/Form - plus that every endpoint answers for its own user only.
"""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_miniapp_token
from app.db.session import get_session
from app.services import workout_stats as ws
from app.webapp import app

TODAY = date(2026, 10, 26)  # a Monday


# --- periods: must match frontend/src/range.tsx ------------------------------


@pytest.mark.parametrize(
    "kind, offset, start, end",
    [
        ("week", 0, date(2026, 10, 25), date(2026, 10, 31)),   # Sunday to Saturday
        ("week", 1, date(2026, 10, 18), date(2026, 10, 24)),
        ("month", 0, date(2026, 10, 1), date(2026, 10, 31)),
        ("month", 10, date(2025, 12, 1), date(2025, 12, 31)),  # across a year
        ("month", 8, date(2026, 2, 1), date(2026, 2, 28)),
        ("year", 0, date(2025, 10, 27), date(2026, 10, 26)),   # 365 days ending today
    ],
)
def test_periods(kind, offset, start, end):
    assert ws.period_for(kind, offset, TODAY) == (start, end)


def test_a_sunday_is_the_first_day_of_its_own_week():
    sunday = date(2026, 10, 25)
    assert ws.period_for("week", 0, sunday)[0] == sunday


# --- sports -------------------------------------------------------------------


@pytest.mark.parametrize(
    "activity_type, sport",
    [("trail_running", "running"), ("road_biking", "cycling"), ("lap_swimming", "swimming"),
     ("strength_training", "strength"), ("hiking", "walking"), ("yoga", "other"), (None, "other")],
)
def test_sports(activity_type, sport):
    assert ws.dashboard_sport(activity_type) == sport


# --- Fitness / Fatigue / Form -------------------------------------------------


def test_known_series():
    (fitness, fatigue, form), = ws.fitness_fatigue([42.0])
    assert fitness == pytest.approx(1.0)
    assert fatigue == pytest.approx(6.0)
    assert form == pytest.approx(-5.0)


def test_rest_drops_fatigue_faster_than_fitness():
    series = ws.fitness_fatigue([100.0] * 30 + [0.0] * 7)
    fitness_before, fatigue_before, _ = series[29]
    fitness_after, fatigue_after, form_after = series[-1]
    assert fatigue_before - fatigue_after > fitness_before - fitness_after
    assert form_after > series[29][2]  # resting makes you fresher


# --- endpoints, against the database ------------------------------------------


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr("app.routers.miniapp.clock.today", lambda: TODAY)


def headers(user):
    return {"Authorization": f"Bearer {create_miniapp_token(user.id)}"}


def run(make_activity, user, day, load=50, kind="running", minutes=60, km=10.0, **raw):
    return make_activity(
        user,
        activity_type=kind,
        start_time=datetime(day.year, day.month, day.day, 7, 0),
        duration_seconds=minutes * 60.0,
        distance_meters=km * 1000,
        raw_json={"activityTrainingLoad": load, **raw},
    )


def test_week_totals_and_hours_by_sport(client, make_user, make_activity):
    user = make_user()
    run(make_activity, user, date(2026, 10, 25), minutes=60, km=10, elevationGain=120, movingDuration=3300)
    run(make_activity, user, date(2026, 10, 26), kind="lap_swimming", minutes=30, km=1.5)
    run(make_activity, user, date(2026, 10, 20), minutes=45, km=8)  # last week

    body = client.get("/api/workouts?kind=week", headers=headers(user)).json()

    assert body["start"] == "2026-10-25" and body["end"] == "2026-10-31"
    assert body["totals"] == {"workouts": 2, "distance_km": 11.5, "moving_hours": 1.4, "elevation_m": 120}
    assert body["previous"]["workouts"] == 1
    assert body["bucket"] == "day"
    assert len(body["hours_by_sport"]) == 7
    sunday, monday = body["hours_by_sport"][:2]
    assert sunday["running"] == 1.0 and monday["swimming"] == 0.5


def test_year_is_bucketed_by_week(client, make_user, make_activity):
    user = make_user()
    run(make_activity, user, date(2026, 1, 15))
    body = client.get("/api/workouts?kind=year", headers=headers(user)).json()
    assert body["bucket"] == "week"
    assert 52 <= len(body["hours_by_sport"]) <= 54
    assert sum(b["running"] for b in body["hours_by_sport"]) == 1.0


def test_load_runs_from_the_first_workout_not_the_period_start(client, make_user, make_activity):
    user = make_user()
    run(make_activity, user, date(2026, 9, 1), load=200)  # before this week
    body = client.get("/api/load?kind=week", headers=headers(user)).json()
    days = body["days"]
    assert days[-1]["date"] == "2026-10-26"  # nothing in the future
    assert days[0]["fitness"] > 0  # remembered from September
    assert days[0]["load"] == 0


def test_week_load_shows_six_weeks_of_context(client, make_user, make_activity):
    user = make_user()
    run(make_activity, user, date(2026, 8, 1))
    days = client.get("/api/load?kind=week", headers=headers(user)).json()["days"]
    assert len(days) == 42
    assert days[0]["date"] == "2026-09-15"


def test_month_load_is_just_the_month(client, make_user, make_activity):
    user = make_user()
    run(make_activity, user, date(2026, 8, 1))
    days = client.get("/api/load?kind=month&offset=1", headers=headers(user)).json()["days"]
    assert days[0]["date"] == "2026-09-01" and days[-1]["date"] == "2026-09-30"


def test_manual_workout_without_load_counts_as_zero_load(client, make_user, make_activity):
    user = make_user()
    make_activity(user, start_time=datetime(2026, 10, 26, 7), raw_json=None)
    body = client.get("/api/load?kind=week", headers=headers(user)).json()
    assert body["days"][-1]["load"] == 0


def test_no_workouts_at_all(client, make_user):
    user = make_user()
    h = headers(user)
    assert client.get("/api/load?kind=month", headers=h).json()["days"] == []
    assert client.get("/api/workouts?kind=month", headers=h).json()["totals"]["workouts"] == 0
    assert client.get("/api/calendar", headers=h).json()["days"] == []


def test_calendar_covers_the_last_365_days(client, make_user, make_activity):
    user = make_user()
    run(make_activity, user, date(2026, 10, 1), load=80)
    run(make_activity, user, date(2026, 10, 1), load=20)
    run(make_activity, user, date(2025, 10, 1), load=99)  # more than a year ago
    body = client.get("/api/calendar", headers=headers(user)).json()
    assert body["days"] == [{"date": "2026-10-01", "load": 100}]


def test_bad_kind_is_rejected(client, make_user):
    assert client.get("/api/workouts?kind=decade", headers=headers(make_user())).status_code == 422


def test_every_endpoint_answers_for_its_own_user_only(client, make_user, make_activity):
    me, other = make_user(), make_user()
    run(make_activity, other, TODAY, load=300)
    h = headers(me)
    assert client.get("/api/workouts?kind=week", headers=h).json()["totals"]["workouts"] == 0
    assert all(d["load"] == 0 for d in client.get("/api/load?kind=week", headers=h).json()["days"])
    assert client.get("/api/calendar", headers=h).json()["days"] == []


def test_endpoints_need_a_token(client):
    for path in ("/api/workouts", "/api/load", "/api/calendar"):
        assert client.get(path).status_code == 401
