"""The chat, the coach and the Mini App must show the watch's own time and pace."""

from types import SimpleNamespace

from app.services.activity_numbers import clock, pace_seconds, timer_seconds, watch_time

# A real run (5 Nov 2026). The watch: 7.01 km, 35:29.8, 5:04 /km.
# Moving time (2104.9 s -> 35:05, 5:00 /km) is NOT what the watch shows.
RUN = SimpleNamespace(
    duration_seconds=2129.839111328125,
    distance_meters=7006.77001953125,
    raw_json={"duration": 2129.839111328125, "movingDuration": 2104.9000244140625,
              "averageSpeed": 3.2899999618530273},
)


def test_time_is_the_watch_timer_to_the_tenth():
    assert watch_time(timer_seconds(RUN)) == "35:29.8"


def test_pace_is_garmins_average_speed():
    assert clock(pace_seconds(RUN)) == "5:04"


def test_long_workouts_and_rounding():
    assert watch_time(3605.97607421875) == "1:00:05.9"
    assert clock(89.5) == "1:30"  # half-up like Math.round, not banker's rounding


def test_manual_entry_falls_back_to_time_over_distance():
    manual = SimpleNamespace(duration_seconds=1500, distance_meters=5000, raw_json=None)
    assert clock(pace_seconds(manual)) == "5:00"
    swim = SimpleNamespace(duration_seconds=1800, distance_meters=1500, raw_json={})
    assert clock(pace_seconds(swim, per_meters=100)) == "2:00"
    assert pace_seconds(SimpleNamespace(duration_seconds=60, distance_meters=50, raw_json={})) is None
    assert watch_time(None) is None
