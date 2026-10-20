"""
Rewards are judged by the workout that crossed the line - so a month of
imported history never turns into a shower of old milestones.
"""

from datetime import timedelta

from app.core import clock
from app.services.achievements import (
    STREAK_MIN_WORKOUTS,
    award_for_activity,
    current_streak_weeks,
    format_achievements,
)


def _run(make_activity, user, km, when=None):
    return make_activity(
        user,
        activity_type="running",
        distance_meters=km * 1000,
        start_time=when or clock.now_local_naive(),
    )


def test_crossing_a_distance_milestone_is_celebrated_once(session, make_user, make_activity):
    me = make_user()
    _run(make_activity, me, 95, clock.now_local_naive() - timedelta(days=10))
    new = _run(make_activity, me, 6)

    earned = award_for_activity(session, new)
    assert {"kind": "distance", "family": "running", "km": 100} in earned
    assert award_for_activity(session, new) == []  # a re-run grants nothing


def test_imported_history_does_not_unlock_old_milestones(session, make_user, make_activity):
    # A month of history (600 km) arrived with the first link. The next run
    # crosses nothing - 100, 250 and 500 were passed long before.
    me = make_user()
    _run(make_activity, me, 600, clock.now_local_naive() - timedelta(days=20))
    new = _run(make_activity, me, 5)

    assert [e for e in award_for_activity(session, new) if e["kind"] == "distance"] == []


def test_first_workout_in_a_sport(session, make_user, make_activity):
    me = make_user()
    _run(make_activity, me, 5, clock.now_local_naive() - timedelta(days=3))
    swim = make_activity(me, activity_type="lap_swimming", distance_meters=1500)

    assert {"kind": "first", "family": "swimming"} in award_for_activity(session, swim)


def test_tenth_workout(session, make_user, make_activity):
    me = make_user()
    for day in range(9):
        make_activity(me, activity_type="strength_training", start_time=clock.now_local_naive() - timedelta(days=40 + day))
    tenth = make_activity(me, activity_type="strength_training")

    assert {"kind": "workouts", "count": 10} in award_for_activity(session, tenth)


def test_four_week_streak(session, make_user, make_activity):
    me = make_user()
    now = clock.now_local_naive()
    for week in range(1, 4):
        for _ in range(STREAK_MIN_WORKOUTS):
            _run(make_activity, me, 3, now - timedelta(weeks=week))
    _run(make_activity, me, 3, now)
    latest = _run(make_activity, me, 3, now)

    assert current_streak_weeks(session, me.id, now.date())[0] == 4
    assert {"kind": "streak", "weeks": 4} in award_for_activity(session, latest)


def test_streak_breaks_on_a_quiet_week(session, make_user, make_activity):
    me = make_user()
    now = clock.now_local_naive()
    for _ in range(STREAK_MIN_WORKOUTS):
        _run(make_activity, me, 3, now)
        _run(make_activity, me, 3, now - timedelta(weeks=2))  # week 1 left empty

    assert current_streak_weeks(session, me.id, now.date())[0] == 1


def test_messages_are_readable():
    text = format_achievements([{"kind": "distance", "family": "running", "km": 1000}])
    assert "1,000" in text and "ריצה" in text


def test_no_first_in_sport_when_history_already_has_that_sport(session, make_user, make_activity):
    me = make_user()
    make_activity(me, activity_type="lap_swimming", distance_meters=1000,
                  start_time=clock.now_local_naive() - timedelta(days=15))
    swim = make_activity(me, activity_type="lap_swimming", distance_meters=1000)

    assert [e for e in award_for_activity(session, swim) if e["kind"] == "first"] == []


def test_a_week_with_a_single_workout_breaks_the_streak(session, make_user, make_activity):
    me = make_user()
    now = clock.now_local_naive()
    for _ in range(STREAK_MIN_WORKOUTS):
        _run(make_activity, me, 3, now)
        _run(make_activity, me, 3, now - timedelta(weeks=2))
    _run(make_activity, me, 3, now - timedelta(weeks=1))  # one only - not enough

    assert current_streak_weeks(session, me.id, now.date())[0] == 1
