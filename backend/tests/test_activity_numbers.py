"""The chat, the coach and the Mini App must show the watch's time and pace."""

from types import SimpleNamespace

from app.services.activity_numbers import clock, pace_seconds

# A real run (5 Nov 2026): 7.01 km, stopped at lights with the timer running.
# Watch: 35:30, 5:04 /km. Moving time (35:05, 5:00) is NOT what the watch shows.
RUN = SimpleNamespace(duration_seconds=2129.839111328125, distance_meters=7006.77001953125)


def test_time_is_the_watch_timer():
    assert clock(RUN.duration_seconds) == "35:30"


def test_pace_is_timer_over_distance_rounded():
    # 303.97 s/km: truncating gave 5:03, one second off the watch.
    assert clock(pace_seconds(RUN)) == "5:04"


def test_hours_and_half_up_rounding():
    assert clock(3605.97607421875) == "1:00:06"
    assert clock(89.5) == "1:30"  # half-up like Math.round, not banker's rounding


def test_swim_pace_per_100m_and_short_distances():
    swim = SimpleNamespace(duration_seconds=1800, distance_meters=1500)
    assert clock(pace_seconds(swim, per_meters=100)) == "2:00"
    assert pace_seconds(SimpleNamespace(duration_seconds=60, distance_meters=50)) is None
    assert clock(None) is None
