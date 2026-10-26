"""
A workout's second-by-second detail: processing Garmin's responses into
the workout page's data, privacy around start and finish, and the page API.
"""

import math
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_miniapp_token
from app.db.session import get_session
from app.models.activity import SOURCE_MANUAL
from app.services import activity_detail as ad
from app.services import activity_page
from app.webapp import app

START = (32.0800, 34.7800)  # Tel Aviv
M_PER_DEG_LAT = 111_195


def run_streams(km=5.0, seconds_per_km=300, hr_start=140, hr_end=140, every=5):
    """A straight run north at a steady pace, one sample every `every` seconds."""
    keys = ["sumDuration", "sumDistance", "directHeartRate", "directSpeed", "directElevation",
            "directLatitude", "directLongitude", "directRunCadence"]
    total = int(km * seconds_per_km)
    speed = 1000 / seconds_per_km
    rows = []
    for t in range(0, total + 1, every):
        d = t * speed
        hr = hr_start + (hr_end - hr_start) * t / total
        rows.append({"metrics": [t, d, hr, speed, 10 + d / 100, START[0] + d / M_PER_DEG_LAT, START[1], 170]})
    return {
        "metricDescriptors": [{"metricsIndex": i, "key": k} for i, k in enumerate(keys)],
        "activityDetailMetrics": rows,
    }


def test_points_are_downsampled_and_keep_the_end():
    detail = ad.build_detail(run_streams(km=10, every=1), {}, [], "running")
    points = detail["points"]
    assert len(points["t"]) <= ad.MAX_POINTS + 1
    assert points["t"][-1] == 3000
    assert len(points["hr"]) == len(points["t"]) == len(points["lat"])


def test_km_splits_from_the_stream():
    detail = ad.build_detail(run_streams(km=5.5), {}, [], "running")
    splits = detail["splits"]
    assert [s["index"] for s in splits] == [1, 2, 3, 4, 5, 6]
    assert all(abs(s["pace"] - 300) <= 5 for s in splits)
    assert splits[-1]["meters"] == 500  # the short last split, its pace still per km


def test_no_splits_for_strength():
    assert ad.build_detail(run_streams(), {}, [], "strength")["splits"] == []


def test_privacy_circles_around_start_and_finish():
    detail = ad.build_detail(run_streams(km=3), {}, [], "running")
    track = detail["track"]
    assert track, "the middle of the run is still drawn"

    def metres(p, q):
        return math.dist((p[0] * M_PER_DEG_LAT, p[1]), (q[0] * M_PER_DEG_LAT, q[1]))

    finish = (START[0] + 3000 / M_PER_DEG_LAT, START[1])
    assert all(metres(p, START) >= ad.PRIVACY_RADIUS_M - 5 for p in track)
    assert all(metres(p, finish) >= ad.PRIVACY_RADIUS_M - 5 for p in track)
    # The chart points' coordinates are blanked the same way.
    assert detail["points"]["lat"][0] is None and detail["points"]["lat"][-1] is None


def test_a_short_loop_near_home_draws_no_track():
    detail = ad.build_detail(run_streams(km=0.3), {}, [], "running")
    assert detail["track"] == []


def test_cardiac_drift():
    steady = ad.build_detail(run_streams(km=6), {}, [], "running")
    drifting = ad.build_detail(run_streams(km=6, hr_start=140, hr_end=160), {}, [], "running")
    assert steady["drift"] == pytest.approx(0, abs=0.5)
    assert drifting["drift"] > 4


def test_no_drift_for_short_runs():
    assert ad.build_detail(run_streams(km=2), {}, [], "running")["drift"] is None


def test_missing_metrics_leave_series_empty():
    streams = run_streams()
    streams["metricDescriptors"] = [d for d in streams["metricDescriptors"] if d["key"] != "directHeartRate"]
    detail = ad.build_detail(streams, {}, [], "running")
    assert detail["points"]["hr"] == [] and detail["points"]["power"] == []


def test_swim_lengths_zones_and_laps():
    laps = {"lapDTOs": [{
        "lapIndex": 1, "distance": 100, "duration": 110, "averageHR": 130, "swimStroke": "FREESTYLE",
        "lengthDTOs": [
            {"duration": 52, "distance": 50, "totalNumberOfStrokes": 18, "averageSwolf": 70},
            {"duration": 0},  # a rest
            {"duration": 55, "distance": 50, "totalNumberOfStrokes": 19, "averageSwolf": 74, "swimStroke": "BACKSTROKE"},
        ],
    }]}
    zones = [{"zoneNumber": 1, "secsInZone": 60.4, "zoneLowBoundary": 100}, {"zoneNumber": 2, "secsInZone": 30}]
    detail = ad.build_detail({}, laps, zones, "swimming")
    assert [(x["stroke"], x["swolf"]) for x in detail["lengths"]] == [("freestyle", 70), ("backstroke", 74)]
    assert detail["zones"][0] == {"zone": 1, "seconds": 60, "low": 100}
    assert detail["laps"][0]["meters"] == 100


def test_nothing_at_all():
    detail = ad.build_detail({}, {}, None, "running")
    assert detail["points"]["t"] == [] and detail["track"] == [] and detail["splits"] == []


# --- the API ------------------------------------------------------------------


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def headers(user):
    return {"Authorization": f"Bearer {create_miniapp_token(user.id)}"}


def test_list_newest_first_with_paging(client, make_user, make_activity):
    user = make_user()
    ids = [make_activity(user, start_time=datetime(2026, 10, d, 7)).id for d in (1, 2, 3)]
    body = client.get("/api/activities?limit=2", headers=headers(user)).json()
    assert [a["id"] for a in body["activities"]] == [ids[2], ids[1]] and body["more"]
    rest = client.get(f"/api/activities?limit=2&before={ids[1]}", headers=headers(user)).json()
    assert [a["id"] for a in rest["activities"]] == [ids[0]] and not rest["more"]


def test_page_with_detail(client, make_user, make_activity, session):
    user = make_user()
    a = make_activity(user, garmin_activity_id=123, raw_json={"elevationGain": 54.0, "averageSwolf": None})
    a.detail = ad.build_detail(run_streams(), {}, [], "running")
    session.add(a)
    session.commit()
    body = client.get(f"/api/activities/{a.id}", headers=headers(user)).json()
    assert body["status"] == "ready"
    assert body["extra"] == {"elevation_gain": 54.0}
    assert body["detail"]["splits"]


def test_opening_a_workout_without_detail_queues_it_once(client, make_user, make_activity, monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.keys = set()

        def exists(self, key):
            return key in self.keys

        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.keys:
                return None
            self.keys.add(key)
            return True

    fake, sent = FakeRedis(), []
    monkeypatch.setattr(activity_page, "_redis", lambda: fake)
    from app.celery_app import celery_app
    monkeypatch.setattr(celery_app, "send_task", lambda name, args: sent.append((name, args)))

    user = make_user()
    a = make_activity(user, garmin_activity_id=456)
    for _ in range(2):
        assert client.get(f"/api/activities/{a.id}", headers=headers(user)).json()["status"] == "pending"
    assert sent == [("app.tasks.fetch_activity_detail_task", [a.id])]

    fake.keys.add(activity_page.THROTTLE_KEY)
    b = make_activity(user, garmin_activity_id=789)
    assert client.get(f"/api/activities/{b.id}", headers=headers(user)).json()["status"] == "busy"


def test_manual_workout_has_no_detail_to_fetch(client, make_user, make_activity):
    user = make_user()
    a = make_activity(user, source=SOURCE_MANUAL)
    assert client.get(f"/api/activities/{a.id}", headers=headers(user)).json()["status"] == "manual"


def test_someone_elses_workout_is_404(client, make_user, make_activity):
    me, other = make_user(), make_user()
    a = make_activity(other)
    assert client.get(f"/api/activities/{a.id}", headers=headers(me)).status_code == 404
    assert client.get("/api/activities", headers=headers(me)).json()["activities"] == []
