"""
The message after a workout: who gets one, for which workouts, and what
is in it.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlmodel import select

import app.tasks as tasks
from app.core import clock
from app.core.claude_client import ClaudeUnavailableError
from app.models.activity import SOURCE_MANUAL
from app.models.coach_event import KIND_POST_WORKOUT, CoachEvent
from app.models.training_plan import TrainingPlan
from app.models.plan_session import DAY_KEYS, PlanSession, week_index
from app.services.post_workout import (
    compose_post_workout,
    fresh_activities_since,
    latest_garmin_activity_id,
    record_post_workout_events,
    workout_facts,
)

_ids = iter(range(5_000_000_000, 6_000_000_000))


def _garmin_run(make_activity, user, hours_ago=1, **fields):
    return make_activity(
        user,
        garmin_activity_id=next(_ids),
        activity_type="running",
        distance_meters=8000,
        duration_seconds=2700,
        start_time=clock.now_local_naive() - timedelta(hours=hours_ago),
        **fields,
    )


# --- which workouts are "new" -------------------------------------------

def test_only_workouts_the_sync_brought_in_count(session, make_user, make_activity):
    me = make_user()
    _garmin_run(make_activity, me)
    before = latest_garmin_activity_id(session, me.id)
    new = _garmin_run(make_activity, me)

    assert [a.id for a in fresh_activities_since(session, me.id, before)] == [new.id]


def test_old_workouts_from_the_first_sync_get_no_message(session, make_user, make_activity):
    # The first sync after linking pulls the last 20 workouts; only the
    # recent ones deserve "nice run!".
    me = make_user()
    _garmin_run(make_activity, me, hours_ago=24 * 10)
    recent = _garmin_run(make_activity, me, hours_ago=2)

    assert [a.id for a in fresh_activities_since(session, me.id, None)] == [recent.id]


def test_manual_workouts_get_no_message(session, make_user, make_activity):
    me = make_user()
    make_activity(me, source=SOURCE_MANUAL, start_time=clock.now_local_naive())
    assert fresh_activities_since(session, me.id, None) == []


# --- the sync wiring -----------------------------------------------------

@pytest.fixture
def sync_bringing(session, make_activity):
    """Replace the Garmin sync with one that 'downloads' the given workouts."""
    def _setup(user, workouts=1, records=None):
        def fake_sync(session, user_id):
            for _ in range(workouts):
                _garmin_run(make_activity, user)
            return list(records or [])

        return patch.object(tasks, "sync_user_garmin_data", side_effect=fake_sync)

    return _setup


@pytest.fixture
def outbox():
    with patch.object(tasks, "send_telegram_message") as sent, \
         patch.object(tasks.deliver_coach_event_task, "delay") as delivered, \
         patch.object(tasks, "_warn_if_watch_stale"):
        yield sent, delivered


RECORD = [{"record_type": "run_5k", "sport": "running", "label": "5 ק\"מ", "value": 1300.0, "previous": 1350.0}]


def test_a_new_workout_becomes_one_event_and_carries_the_record(session, make_user, sync_bringing, outbox):
    sent, delivered = outbox
    me = make_user()
    with sync_bringing(me, records=RECORD):
        tasks._sync_one_user(tasks.sync_one_user_task, me.id)

    [event] = session.exec(select(CoachEvent).where(CoachEvent.user_id == me.id)).all()
    assert event.kind == KIND_POST_WORKOUT and event.payload["records"] == RECORD
    delivered.assert_called_once_with(event.id)
    sent.assert_not_called()  # the record is inside the workout message, not beside it


def test_a_second_sync_of_the_same_workouts_sends_nothing_new(session, make_user, sync_bringing, outbox):
    _, delivered = outbox
    me = make_user()
    with sync_bringing(me, workouts=2):
        tasks._sync_one_user(tasks.sync_one_user_task, me.id)
    with sync_bringing(me, workouts=0):
        tasks._sync_one_user(tasks.sync_one_user_task, me.id)

    assert delivered.call_count == 2


def test_user_who_turned_it_off_still_gets_the_record_message(session, make_user, sync_bringing, outbox):
    sent, delivered = outbox
    me = make_user(notify_post_workout=False)
    with sync_bringing(me, records=RECORD):
        tasks._sync_one_user(tasks.sync_one_user_task, me.id)

    delivered.assert_not_called()
    sent.assert_called_once()  # the separate 🏆 message, as before


# --- what the message says ------------------------------------------------

def _event_for(session, user, activity, **payload):
    [event] = record_post_workout_events(session, user, [activity], [])
    event.payload = {**event.payload, **payload}
    return event


def test_message_has_numbers_note_and_rewards(session, make_user, make_activity):
    me = make_user()
    run = _garmin_run(make_activity, me)
    event = _event_for(session, me, run, records=RECORD)

    with patch("app.services.post_workout.generate_post_workout_note", return_value="ריצה יציבה וטובה."):
        text = compose_post_workout(session, me, event)

    assert "ריצה יציבה וטובה." in text
    assert "שיא אישי" in text
    assert "🌱" in text  # first run ever -> first-in-sport reward


def test_message_still_goes_out_when_the_ai_is_down(session, make_user, make_activity):
    me = make_user()
    run = _garmin_run(make_activity, me)
    event = _event_for(session, me, run)

    with patch("app.services.post_workout.generate_post_workout_note", side_effect=ClaudeUnavailableError("down")):
        text = compose_post_workout(session, me, event)

    assert text  # numbers and rewards, without the paragraph


def test_another_users_workout_is_never_described(session, make_user, make_activity):
    me, other = make_user(), make_user()
    theirs = _garmin_run(make_activity, other)
    event = _event_for(session, me, _garmin_run(make_activity, me))
    event.payload = {"activity_id": theirs.id}

    assert compose_post_workout(session, me, event) is None


def test_facts_include_what_was_planned_for_that_day(session, make_user, make_activity):
    me = make_user()
    run = _garmin_run(make_activity, me, hours_ago=0)
    plan = TrainingPlan(user_id=me.id, discipline="running", plan="p", is_active=True)
    session.add(plan)
    session.commit()
    session.add(PlanSession(
        user_id=me.id, plan_id=plan.id, day_of_week=week_index(run.start_time.date()), title="אינטרוולים",
    ))
    session.commit()

    facts = workout_facts(session, run, [])

    assert [p["title"] for p in facts["planned_today"]] == ["אינטרוולים"]
    assert DAY_KEYS[week_index(run.start_time.date())] == facts["planned_today"][0]["day"]
