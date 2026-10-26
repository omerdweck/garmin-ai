"""
A workout deleted in Garmin Connect disappears here on the next sync -
and nothing else does: not manual entries, not older workouts, and not
everything at once when Garmin's answer looks broken.
"""

from datetime import datetime, timedelta

from sqlmodel import select

from app.core import clock
from app.models.activity import SOURCE_MANUAL, Activity
from app.services.garmin_sync import DELETION_WINDOW_DAYS, remove_deleted_activities


class FakeGarmin:
    def __init__(self, ids):
        self.ids = ids
        self.calls = []

    def get_activities_by_date(self, start, end):
        self.calls.append((start, end))
        return [{"activityId": i} for i in self.ids]


def stored(session, user, garmin_id, days_ago, source="garmin"):
    row = Activity(
        user_id=user.id, garmin_activity_id=garmin_id, source=source, activity_type="running",
        start_time=datetime.combine(clock.today() - timedelta(days=days_ago), datetime.min.time()) + timedelta(hours=7),
        duration_seconds=1800,
    )
    session.add(row)
    session.commit()
    return row


def remaining(session, user):
    session.expire_all()
    return sorted(
        a.garmin_activity_id or -a.id for a in session.exec(select(Activity).where(Activity.user_id == user.id))
    )


def test_a_workout_deleted_in_garmin_is_removed(session, make_user):
    user = make_user()
    stored(session, user, 1, 2)
    stored(session, user, 2, 3)
    stored(session, user, 3, 5)

    removed = remove_deleted_activities(session, user.id, FakeGarmin({1, 3}))

    assert removed == 1
    assert remaining(session, user) == [1, 3]


def test_manual_workouts_are_never_touched(session, make_user):
    user = make_user()
    manual = stored(session, user, None, 1, source=SOURCE_MANUAL)
    stored(session, user, 7, 1)
    remove_deleted_activities(session, user.id, FakeGarmin({7}))
    assert session.get(Activity, manual.id) is not None


def test_older_than_the_window_is_left_alone(session, make_user):
    user = make_user()
    stored(session, user, 9, DELETION_WINDOW_DAYS + 10)
    stored(session, user, 8, DELETION_WINDOW_DAYS)  # the edge day: left alone too
    stored(session, user, 1, 1)
    remove_deleted_activities(session, user.id, FakeGarmin({1}))
    assert remaining(session, user) == [1, 8, 9]


def test_an_empty_answer_with_several_stored_removes_nothing(session, make_user):
    user = make_user()
    for i in range(4):
        stored(session, user, i + 1, i + 1)
    assert remove_deleted_activities(session, user.id, FakeGarmin(set())) == 0
    assert len(remaining(session, user)) == 4


def test_deleting_the_only_recent_workout_still_works(session, make_user):
    user = make_user()
    stored(session, user, 5, 1)
    assert remove_deleted_activities(session, user.id, FakeGarmin(set())) == 1


def test_only_this_users_workouts(session, make_user):
    me, other = make_user(), make_user()
    stored(session, other, 42, 1)
    stored(session, me, 1, 1)
    remove_deleted_activities(session, me.id, FakeGarmin({1}))
    assert remaining(session, other) == [42]


def test_asks_garmin_for_the_window(session, make_user):
    user = make_user()
    garmin = FakeGarmin(set())
    remove_deleted_activities(session, user.id, garmin)
    start, end = garmin.calls[0]
    assert end == clock.today().isoformat()
    assert start == (clock.today() - timedelta(days=DELETION_WINDOW_DAYS)).isoformat()
