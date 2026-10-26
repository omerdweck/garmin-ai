"""
The today, health, goals and records tabs: shapes the page relies on,
the personal-baseline logic of the readiness rings, and that every
endpoint answers for its own user only.
"""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_miniapp_token
from app.db.session import get_session
from app.models.daily_metric import DailyMetric
from app.models.metric_alert import MetricAlert, MetricBaseline
from app.models.personal_record import PersonalRecord
from app.models.workout import STATUS_SENT, StoredWorkout
from app.services import dashboard_stats as ds
from app.webapp import app

TODAY = date(2026, 10, 26)


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setattr("app.routers.miniapp.clock.today", lambda: TODAY)
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def headers(user):
    return {"Authorization": f"Bearer {create_miniapp_token(user.id)}"}


def day(session, user, d, **fields):
    session.add(DailyMetric(user_id=user.id, date=d, raw_json={"big": "x" * 10}, **fields))
    session.commit()


# --- rings --------------------------------------------------------------------


def test_ring_against_personal_band():
    band = {"mean": 50.0, "low": 45.0, "high": 55.0}
    assert ds._ring("hrv", "HRV", 60, TODAY, None, band, True)["state"] == "good"
    assert ds._ring("hrv", "HRV", 40, TODAY, None, band, True)["state"] == "low"
    # Resting HR: lower is better, so the same numbers flip.
    assert ds._ring("rhr", "x", 60, TODAY, None, band, False)["state"] == "low"


def test_ring_on_a_0_to_100_scale():
    ring = ds._ring("bb", "x", 80, TODAY, 100, None, True)
    assert ring["fill"] == 0.8 and ring["state"] == "good"


def test_ring_without_a_value():
    assert ds._ring("bb", "x", None, None, 100, None, True)["state"] == "unknown"


def test_workout_blocks_expand_repeats():
    spec = {"steps": [
        {"kind": "warmup", "seconds": 600},
        {"kind": "repeat", "times": 3, "steps": [{"kind": "interval", "meters": 1000}, {"kind": "recovery", "seconds": 90}]},
        {"kind": "cooldown", "seconds": 300},
    ]}
    kinds = [b["kind"] for b in ds._workout_blocks(spec, "running")]
    assert kinds == ["warmup"] + ["interval", "recovery"] * 3 + ["cooldown"]


# --- endpoints ----------------------------------------------------------------


def test_today(client, session, make_user):
    user = make_user()
    day(session, user, TODAY, body_battery_high=82, avg_hrv=62, sleep_score=71, resting_heart_rate=48)
    session.add(MetricBaseline(user_id=user.id, metric="avg_hrv", mean=50, std=5, samples=28,
                               window_start=TODAY - timedelta(days=28), window_end=TODAY))
    session.add(MetricAlert(user_id=user.id, rule="rhr_high", started_on=TODAY - timedelta(days=2), last_seen_on=TODAY))
    session.add(StoredWorkout(user_id=user.id, sport="running", name="אינטרוולים", status=STATUS_SENT,
                              scheduled_for=TODAY, spec={"steps": [{"kind": "warmup", "seconds": 600}]}))
    session.commit()

    body = client.get("/api/today", headers=headers(user)).json()

    rings = {r["key"]: r for r in body["rings"]}
    assert rings["body_battery"]["value"] == 82
    assert rings["hrv"]["state"] == "good"
    assert body["alert"]["metric"] == "resting_heart_rate"
    assert body["workouts"][0]["on_watch"] is True
    assert body["workouts"][0]["blocks"] == [{"kind": "warmup", "seconds": 600}]


def test_today_falls_back_to_yesterdays_sleep(client, session, make_user):
    user = make_user()
    day(session, user, TODAY - timedelta(days=1), sleep_score=77)
    day(session, user, TODAY)
    sleep = next(r for r in client.get("/api/today", headers=headers(user)).json()["rings"] if r["key"] == "sleep")
    assert sleep["value"] == 77 and sleep["as_of"] == "2026-10-25"


def test_health_metrics(client, session, make_user):
    user = make_user()
    for i in range(10):
        day(session, user, TODAY - timedelta(days=i), resting_heart_rate=50 + i,
            moderate_intensity_minutes=10, vigorous_intensity_minutes=5,
            sleep_duration_minutes=420, deep_sleep_minutes=80, light_sleep_minutes=250, rem_sleep_minutes=90)
    session.add(MetricAlert(user_id=user.id, rule="rhr_high", started_on=TODAY - timedelta(days=3), last_seen_on=TODAY))
    session.commit()

    body = client.get("/api/health-metrics?kind=month", headers=headers(user)).json()

    rhr = body["metrics"]["resting_heart_rate"]
    assert len(rhr["days"]) == 10
    assert rhr["alerts"] == [{"start": "2026-10-23", "end": "2026-10-26"}]
    assert rhr["last_week"] is not None  # Oct 18-24
    assert body["metrics"]["intensity_minutes"]["days"][0][1] == 20  # moderate + 2 x vigorous
    assert body["metrics"]["spo2"]["days"] == []
    assert body["sleep"][0]["deep"] == 80
    assert body["days_recorded"] == 10


def test_liveness_check_still_answers():
    assert TestClient(app).get("/api/health").json() == {"status": "ok"}


def test_goals(client, session, make_user, make_activity):
    user = make_user()
    day(session, user, TODAY - timedelta(days=100), race_predict_5k_seconds=1500, vo2_max=50.0)
    day(session, user, TODAY, race_predict_5k_seconds=1440)
    session.add(StoredWorkout(user_id=user.id, sport="running", name="ארוכה", status=STATUS_SENT,
                              scheduled_for=date(2026, 10, 30), spec={"steps": []}))
    session.commit()
    make_activity(user, start_time=datetime(2026, 10, 25, 7))

    body = client.get("/api/goals", headers=headers(user)).json()

    five = body["races"][0]
    assert five["key"] == "5k" and five["now"] == 1440
    assert five["before"] == 1500  # the reading from about three months ago
    assert body["vo2max"] == [["2026-07-18", 50.0]]
    assert [len(d["done"]) for d in body["week"]][:2] == [1, 0]
    assert body["week"][5]["planned"][0]["name"] == "ארוכה"


def test_records_recompute_milestones_from_workouts(client, session, make_user, make_activity):
    user = make_user()
    for i in range(6):
        make_activity(user, start_time=datetime(2026, 1, 1 + i, 7), distance_meters=10_000)
    session.add(PersonalRecord(user_id=user.id, record_type="run_5k", value=1385, achieved_at=date(2026, 3, 1)))
    session.commit()

    body = client.get("/api/records", headers=headers(user)).json()

    assert any("50 ק\"מ" in r["text"] for r in body["reached"])
    nxt = next(n for n in body["next"] if "ריצה" in n["text"])
    assert nxt["progress"] == 60 and nxt["target"] == 100
    assert body["personal_records"][0]["value"] == "23:05"
    assert body["year"]["workouts"] == 6 and body["year"]["top_sport"] == "running"


def test_each_endpoint_answers_for_its_own_user_only(client, session, make_user, make_activity):
    me, other = make_user(), make_user()
    day(session, other, TODAY, body_battery_high=90, race_predict_5k_seconds=1300)
    make_activity(other, start_time=datetime(2026, 10, 25, 7))
    h = headers(me)
    assert all(r["value"] is None for r in client.get("/api/today", headers=h).json()["rings"])
    assert client.get("/api/health-metrics?kind=week", headers=h).json()["days_recorded"] == 0
    assert client.get("/api/goals", headers=h).json()["races"] == []
    assert client.get("/api/records", headers=h).json()["year"]["workouts"] == 0


def test_endpoints_need_a_token(client):
    for path in ("/api/today", "/api/health-metrics", "/api/goals", "/api/records"):
        assert client.get(path).status_code == 401
