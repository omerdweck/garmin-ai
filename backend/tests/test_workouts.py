"""
Workouts end to end, with a fake Garmin: the coach's tool, who may send
what, what reaches Garmin and how often, and that nobody can act on
another user's workout.
"""

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlmodel import select

import app.tasks as tasks
from app.bot.workouts import workout_keyboard
from app.core import clock
from app.core.claude_client import TOOLS, _execute_tool
from app.core.config import settings
from app.core.garmin_client import GarminRateLimitError, GarminRequestError
from app.models.athlete_profile import AthleteProfile
from app.models.workout import STATUS_DRAFT, STATUS_REMOVED, STATUS_SENT, StoredWorkout
from app.services import garmin_throttle
from app.services import workouts as service
from app.services.workout_schema import parse_workout
from tests.workout_examples import EXAMPLES


class FakeGarmin:
    def __init__(self):
        self.calls = []
        self.next_id = 700

    def upload_workout(self, payload):
        self.calls.append(("upload", payload["workoutName"]))
        self.next_id += 1
        return {"workoutId": self.next_id, **payload}

    def schedule_workout(self, workout_id, day):
        self.calls.append(("schedule", workout_id, day))
        return {"workoutScheduleId": 9000 + len(self.calls)}

    def unschedule_workout(self, scheduled_id):
        self.calls.append(("unschedule", scheduled_id))

    def download_workout(self, workout_id):
        self.calls.append(("download", workout_id))
        return b".FIT-bytes"

    def delete_workout(self, workout_id):
        self.calls.append(("delete", workout_id))


@pytest.fixture
def garmin():
    fake = FakeGarmin()
    with patch.object(service, "_garmin", return_value=fake):
        yield fake


@pytest.fixture
def owner(make_user, monkeypatch):
    user = make_user()
    monkeypatch.setattr(settings, "admin_chat_id", user.telegram_chat_id)
    return user


def stored(session, user, sport="running"):
    workout, _ = parse_workout(EXAMPLES[sport])
    return service.save_workout(session, user.id, workout)


def tool(session, user, name, payload):
    return json.loads(_execute_tool(session, user, name, payload))


# --- the coach's tool ---------------------------------------------------------

def test_tools_are_registered():
    names = {t["name"] for t in TOOLS}
    assert {"build_workout", "search_exercises"} <= names


def test_a_valid_workout_is_saved_for_the_caller(session, make_user):
    other, me = make_user(), make_user()  # the caller is not user #1
    result = tool(session, me, "build_workout", EXAMPLES["running"])

    assert result["saved"] is True and "5x1K" in result["preview"]
    row = session.get(StoredWorkout, result["workout_id"])
    assert row.user_id == me.id and row.status == STATUS_DRAFT
    assert session.exec(select(StoredWorkout).where(StoredWorkout.user_id == other.id)).all() == []


def test_an_invalid_workout_comes_back_as_errors_and_is_not_saved(session, make_user):
    me = make_user()
    bad = {**EXAMPLES["running"], "steps": [{"kind": "interval", "seconds": 60, "target": {"type": "heart_rate_zone", "zone": 9}}]}
    result = tool(session, me, "build_workout", bad)

    assert result["saved"] is False and any("zone 1-5" in e for e in result["errors"])
    assert session.exec(select(StoredWorkout)).all() == []


def test_ftp_percent_without_ftp_warns_the_coach(session, make_user):
    me = make_user()
    assert tool(session, me, "build_workout", EXAMPLES["cycling"])["warnings"]
    session.add(AthleteProfile(user_id=me.id, ftp_watts=240))
    session.commit()
    assert tool(session, me, "build_workout", EXAMPLES["cycling"])["warnings"] == []


def test_exercise_search(session, make_user):
    found = tool(session, make_user(), "search_exercises", {"query": "barbell squat"})["exercises"]
    assert "Barbell Back Squat" in found[:5]


# --- who may send --------------------------------------------------------------

def test_only_the_owner_can_send_until_a_sport_is_opened(make_user, owner, monkeypatch):
    someone = make_user()
    assert service.can_send_to_watch(owner, "swimming")
    assert not service.can_send_to_watch(someone, "running")
    monkeypatch.setattr(settings, "workout_upload_sports", "running, cycling")
    assert service.can_send_to_watch(someone, "running")
    assert not service.can_send_to_watch(someone, "swimming")


def test_a_closed_sport_never_reaches_garmin(session, make_user, garmin):
    someone = make_user()
    row = stored(session, someone)
    with pytest.raises(PermissionError):
        service.send_to_watch(session, someone, row.id, clock.today())
    with pytest.raises(PermissionError):
        service.fit_file(session, someone, row.id)
    assert garmin.calls == []


def test_another_users_workout_is_untouchable(session, make_user, owner, garmin):
    mine = stored(session, make_user())
    for action in (
        lambda: service.send_to_watch(session, owner, mine.id, clock.today()),
        lambda: service.fit_file(session, owner, mine.id),
        lambda: service.remove_from_watch(session, owner, mine.id),
    ):
        with pytest.raises(service.WorkoutNotFound):
            action()
    assert garmin.calls == [] and service.discard_draft(session, owner.id, mine.id) is False


# --- what reaches Garmin ----------------------------------------------------------

def test_sending_uploads_once_and_schedules(session, owner, garmin):
    row = stored(session, owner)
    today = clock.today()

    service.send_to_watch(session, owner, row.id, today)
    service.send_to_watch(session, owner, row.id, today)  # pressed twice

    assert garmin.calls == [("upload", "5x1K"), ("schedule", 701, today.isoformat())]
    session.refresh(row)
    assert row.status == STATUS_SENT and row.garmin_workout_id == 701
    assert row.sent_json["workoutName"] == "5x1K"  # what was sent is on record


def test_moving_to_another_day_reschedules_without_a_second_upload(session, owner, garmin):
    row = stored(session, owner)
    today, tomorrow = clock.today(), clock.today() + timedelta(days=1)
    service.send_to_watch(session, owner, row.id, today)
    first_schedule = session.get(StoredWorkout, row.id).garmin_schedule_id

    service.send_to_watch(session, owner, row.id, tomorrow)

    assert [c[0] for c in garmin.calls] == ["upload", "schedule", "unschedule", "schedule"]
    assert garmin.calls[2] == ("unschedule", first_schedule)


def test_a_fit_file_for_an_unsent_workout_leaves_nothing_behind(session, owner, garmin):
    row = stored(session, owner)
    name, content = service.fit_file(session, owner, row.id)

    assert (name, content) == ("5x1K.fit", b".FIT-bytes")
    assert [c[0] for c in garmin.calls] == ["upload", "download", "delete"]
    session.refresh(row)
    assert row.garmin_workout_id is None and row.status == STATUS_DRAFT


def test_a_fit_file_for_a_sent_workout_is_just_downloaded(session, owner, garmin):
    row = stored(session, owner)
    service.send_to_watch(session, owner, row.id, clock.today())
    garmin.calls.clear()
    service.fit_file(session, owner, row.id)
    assert garmin.calls == [("download", 701)]


def test_removing_deletes_it_from_garmin(session, owner, garmin):
    row = stored(session, owner)
    service.send_to_watch(session, owner, row.id, clock.today())
    service.remove_from_watch(session, owner, row.id)

    assert garmin.calls[-1] == ("delete", 701)
    assert session.get(StoredWorkout, row.id).status == STATUS_REMOVED
    with pytest.raises(service.WorkoutNotFound):
        service.load_workout(session, owner.id, row.id)


# --- the worker tasks -------------------------------------------------------------

@pytest.fixture
def outbox():
    with patch.object(tasks, "send_telegram_message") as sent, patch.object(tasks, "send_telegram_document") as files:
        yield sent, files


def test_send_task_confirms_with_a_remove_button(session, owner, garmin, outbox):
    sent, _ = outbox
    row = stored(session, owner)
    tasks.send_workout_task(owner.id, row.id, clock.today().isoformat())

    text = sent.call_args.args[1]
    assert "✅" in text and "5x1K" in text
    button = sent.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data == f"wk:drop:{row.id}"


def test_garmin_refusing_the_json_is_reported_not_crashed(session, owner, outbox):
    sent, _ = outbox
    row = stored(session, owner)
    with patch.object(service, "_garmin", return_value=FakeGarmin()), \
         patch.object(service, "upload_workout", side_effect=GarminRequestError("400 bad step")):
        tasks.send_workout_task(owner.id, row.id, clock.today().isoformat())

    assert "⚠️" in sent.call_args.args[1]
    session.refresh(row)
    assert row.sent_json is not None and row.status == STATUS_DRAFT


def test_throttling_pauses_everyone(session, owner, outbox):
    garmin_throttle._redis().delete(garmin_throttle.KEY)
    row = stored(session, owner)
    with patch.object(service, "_garmin", side_effect=GarminRateLimitError("429")):
        tasks.workout_fit_task(owner.id, row.id)
    assert garmin_throttle.is_throttled()
    garmin_throttle._redis().delete(garmin_throttle.KEY)


def test_fit_task_sends_the_file(session, owner, garmin, outbox):
    _, files = outbox
    row = stored(session, owner)
    tasks.workout_fit_task(owner.id, row.id)
    assert files.call_args.args[1:3] == (b".FIT-bytes", "5x1K.fit")


def test_workout_tasks_stay_on_the_default_queue():
    from app.celery_app import celery_app

    routes = celery_app.conf.task_routes or {}
    for name in ("send_workout_task", "workout_fit_task", "remove_workout_task"):
        assert f"app.tasks.{name}" not in routes


# --- the bot side -------------------------------------------------------------------

def _buttons(keyboard):
    return [b.callback_data for row in keyboard.inline_keyboard for b in row]


def test_owner_sees_the_fit_button_others_only_trainer_files(session, make_user, owner):
    someone = make_user()
    workout, _ = parse_workout(EXAMPLES["cycling"])
    row = service.save_workout(session, owner.id, workout)

    mine = _buttons(workout_keyboard(row, workout, owner, ftp=250))
    assert {f"wk:file:{row.id}:{f}" for f in ("fit", "zwo", "erg", "mrc")} <= set(mine)

    theirs = _buttons(workout_keyboard(row, workout, someone, ftp=None))
    assert not any(b.endswith(":fit") for b in theirs)
    assert f"wk:file:{row.id}:zwo" in theirs  # trainer files need no Garmin
