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
        rows.append({"metrics": [t, d, hr, speed, 10 + d / 100, START[0] + d / M_PER_DEG_LAT, START[1], 85]})
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


def test_run_cadence_counts_both_feet():
    # Garmin's directRunCadence is one foot (85); the page shows steps a minute.
    detail = ad.build_detail(run_streams(km=1), {}, [], "running")
    assert set(detail["points"]["cad"]) == {170}


def test_full_cadence_is_used_as_is():
    streams = run_streams(km=1)
    streams["metricDescriptors"].append({"metricsIndex": 8, "key": "directDoubleCadence"})
    for row in streams["activityDetailMetrics"]:
        row["metrics"].append(168)
    assert set(ad.build_detail(streams, {}, [], "running")["points"]["cad"]) == {168}


def test_bike_cadence_is_not_doubled():
    streams = run_streams(km=1)
    streams["metricDescriptors"][7]["key"] = "directBikeCadence"
    assert set(ad.build_detail(streams, {}, [], "cycling")["points"]["cad"]) == {85}


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


# --- The watch's own numbers ------------------------------------------------

def auto_laps(*laps):
    """(metres, timer seconds, moving seconds, average speed) per lap."""
    return {"lapDTOs": [
        {"lapIndex": i, "distance": m, "duration": t, "movingDuration": mv, "averageSpeed": v,
         "averageHR": 150, "elevationGain": 4}
        for i, (m, t, mv, v) in enumerate(laps, start=1)
    ]}


def test_km_splits_are_the_watchs_auto_laps():
    # Km 2 had a stop at a light with the timer running: the watch says
    # 5:30 for it, moving time would say 5:05.
    laps = auto_laps((1000.4, 301.2, 301.0, 3.32), (999.1, 330.6, 305.0, 3.03), (512.0, 150.3, 150.3, 3.41))
    splits = ad.build_detail(run_streams(), laps, [], "running")["splits"]
    assert [s["meters"] for s in splits] == [1000, 1000, 512]
    assert [s["seconds"] for s in splits] == [301.2, 330.6, 150.3]
    assert splits[1]["pace"] == round(1000 / 3.03, 1)  # Garmin's lap speed, not ours
    assert splits[0]["avg_hr"] == 150


def test_manual_laps_fall_back_to_stream_splits():
    laps = auto_laps((400, 80, 80, 5.0), (1600, 480, 480, 3.33))  # intervals, not per km
    splits = ad.build_detail(run_streams(), laps, [], "running")["splits"]
    assert [s["index"] for s in splits] == [1, 2, 3, 4, 5]
    assert all(s["meters"] == 1000 for s in splits)


def test_laps_keep_timer_time_not_moving_time():
    laps = auto_laps((1000, 330.6, 305.0, 3.03), (1000, 300, 300, 3.33))
    assert ad.build_detail({}, laps, [], "running")["laps"][0]["seconds"] == 330.6


def test_a_detail_built_by_older_code_is_fetched_again(client, make_user, make_activity, session, monkeypatch):
    monkeypatch.setattr(activity_page, "request_detail", lambda activity_id: "pending")
    user = make_user()
    a = make_activity(user, garmin_activity_id=321)
    a.detail = {**ad.build_detail(run_streams(), {}, [], "running"), "version": 2}
    session.add(a)
    session.commit()
    body = client.get(f"/api/activities/{a.id}", headers=headers(user)).json()
    assert body["status"] == "pending"
    assert body["detail"]["splits"]  # the old one stays on screen meanwhile
    assert ad.is_current(ad.build_detail(run_streams(), {}, [], "running"))
    assert not ad.is_current(None)
