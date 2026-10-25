"""
Workouts with a day: choosing it, saving in the bot or sending to the
watch, the week view, and the builder's time / distance / typed amounts.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.tasks as tasks
from app.bot import workouts as bot
from app.bot.plans import week_keyboard
from app.core import clock
from app.core.config import settings
from app.models.plan_session import PlanSession, week_index
from app.models.training_plan import TrainingPlan
from app.models.workout import STATUS_DRAFT, STATUS_PLANNED, STATUS_REMOVED, STATUS_SENT, StoredWorkout
from app.services import workouts as service
from app.services.plan_view import day_label, format_upcoming_week
from app.services.workout_schema import parse_workout
from tests.workout_examples import EXAMPLES

TODAY = None


@pytest.fixture(autouse=True)
def _today():
    global TODAY
    TODAY = clock.today()


@pytest.fixture
def owner(make_user, monkeypatch):
    user = make_user()
    monkeypatch.setattr(settings, "admin_chat_id", user.telegram_chat_id)
    return user


def stored(session, user, sport="running", **changes):
    workout, errors = parse_workout({**EXAMPLES[sport], **changes})
    assert not errors
    return service.save_workout(session, user.id, workout)


def buttons(keyboard):
    return [b.callback_data for row in keyboard.inline_keyboard for b in row]


def labels(keyboard):
    return [b.text for row in keyboard.inline_keyboard for b in row]


def keyboard_for(session, row, user):
    row_, workout = service.load_workout(session, user.id, row.id)
    return bot.workout_keyboard(row_, workout, user, ftp=None)


# --- the service ---------------------------------------------------------------

def test_saving_for_a_day_plans_it_in_the_bot(session, make_user):
    me = make_user()
    row = stored(session, me)
    day = TODAY + timedelta(days=3)

    service.plan_workout(session, me.id, row.id, day)

    session.refresh(row)
    assert (row.status, row.scheduled_for, row.spec["date"]) == (STATUS_PLANNED, day, day.isoformat())
    assert row.garmin_workout_id is None  # Garmin untouched


def test_nobody_can_plan_another_users_workout(session, make_user):
    row = stored(session, make_user())
    with pytest.raises(service.WorkoutNotFound):
        service.plan_workout(session, make_user().id, row.id, TODAY)


def test_a_workout_on_the_watch_is_moved_through_the_watch(session, make_user):
    me = make_user()
    row = stored(session, me)
    row.status = STATUS_SENT
    session.add(row)
    session.commit()
    with pytest.raises(ValueError):
        service.plan_workout(session, me.id, row.id, TODAY)


def test_planned_workouts_can_be_deleted_sent_ones_cannot_here(session, make_user):
    me = make_user()
    planned = stored(session, me)
    service.plan_workout(session, me.id, planned.id, TODAY)
    assert service.discard_draft(session, me.id, planned.id)
    assert session.get(StoredWorkout, planned.id).status == STATUS_REMOVED

    sent = stored(session, me)
    sent.status = STATUS_SENT
    session.add(sent)
    session.commit()
    assert not service.discard_draft(session, me.id, sent.id)


def test_scheduled_between_is_this_users_saved_days_only(session, make_user):
    me, other = make_user(), make_user()
    in_range = stored(session, me)
    service.plan_workout(session, me.id, in_range.id, TODAY + timedelta(days=2))
    stored(session, me)  # a draft without a day
    later = stored(session, me)
    service.plan_workout(session, me.id, later.id, TODAY + timedelta(days=20))
    theirs = stored(session, other)
    service.plan_workout(session, other.id, theirs.id, TODAY)

    found = service.scheduled_between(session, me.id, TODAY, TODAY + timedelta(days=6))
    assert [w.id for w in found] == [in_range.id]


# --- the week view -------------------------------------------------------------------

def test_week_shows_saved_workouts_even_without_a_plan(session, make_user):
    me = make_user()
    assert format_upcoming_week(session, me.id) == ""
    row = stored(session, me)
    service.plan_workout(session, me.id, row.id, TODAY + timedelta(days=1))

    text = format_upcoming_week(session, me.id)
    assert "5x1K" in text and "💾 בבוט" in text and "השבוע שלך" in text


def test_week_merges_plan_and_saved_workouts_on_the_same_day(session, make_user):
    me = make_user()
    plan = TrainingPlan(user_id=me.id, discipline="running", plan="p", is_active=True)
    session.add(plan)
    session.commit()
    session.add(PlanSession(user_id=me.id, plan_id=plan.id, day_of_week=week_index(TODAY), title="ריצה קלה"))
    session.commit()
    row = stored(session, me, sport="strength")
    service.plan_workout(session, me.id, row.id, TODAY)

    today_block = format_upcoming_week(session, me.id).split("(מחר)")[0]
    assert "ריצה קלה" in today_block and "Legs" in today_block


def test_week_keyboard_adds_to_every_day_and_opens_saved_ones(session, make_user):
    me = make_user()
    row = stored(session, me)
    service.plan_workout(session, me.id, row.id, TODAY)

    keyboard = week_keyboard([row], has_plan=False)
    adds = [b for b in buttons(keyboard) if b.startswith("wkb:day:")]
    assert adds == [f"wkb:day:{(TODAY + timedelta(days=i)).strftime('%Y%m%d')}" for i in range(7)]
    assert f"wk:open:{row.id}" in buttons(keyboard)
    assert "plan:delete" not in buttons(keyboard)


# --- the buttons under a workout -------------------------------------------------------

def test_a_draft_without_a_day_asks_for_one(session, owner):
    row = stored(session, owner)
    assert buttons(keyboard_for(session, row, owner))[0] == f"wk:day:{row.id}"


def test_a_draft_the_coach_dated_offers_that_day_at_once(session, owner):
    day = TODAY + timedelta(days=2)
    row = stored(session, owner, date=day.isoformat())
    code = day.strftime("%Y%m%d")

    keyboard = keyboard_for(session, row, owner)
    assert f"wk:save:{row.id}:{code}" in buttons(keyboard)
    assert f"wk:send:{row.id}:{code}" in buttons(keyboard)
    assert any(day_label(day) in text for text in labels(keyboard))


def test_without_watch_access_there_is_only_save(session, make_user):
    me = make_user()
    row = stored(session, me, date=TODAY.isoformat())
    keyboard = keyboard_for(session, row, me)
    assert not any(b.startswith("wk:send") for b in buttons(keyboard))
    assert any(b.startswith("wk:save") for b in buttons(keyboard))


def test_picker_offers_the_next_seven_days(session, owner):
    row = stored(session, owner)
    picked = [b for b in buttons(bot.picker_keyboard(row)) if b.startswith("wk:pick:")]
    assert len(picked) == 7
    assert picked[0].endswith(TODAY.strftime("%Y%m%d"))
    assert labels(bot.picker_keyboard(row))[:2] == ["היום", "מחר"]


def test_planned_and_sent_workouts_get_their_own_buttons(session, owner):
    row = stored(session, owner)
    service.plan_workout(session, owner.id, row.id, TODAY + timedelta(days=1))
    planned = keyboard_for(session, row, owner)
    assert f"wk:day:{row.id}" in buttons(planned) and "🗑 מחיקה" in labels(planned)

    row.status = STATUS_SENT
    session.add(row)
    session.commit()
    _, workout = service.load_workout(session, owner.id, row.id)
    sent = bot.workout_keyboard(row, workout, owner, ftp=None)
    assert "🗑 הסרה מהשעון" in labels(sent)
    confirm = bot.confirm_keyboard(row, workout, owner, TODAY + timedelta(days=3))
    assert not any(b.startswith("wk:save") for b in buttons(confirm))  # moving it moves the watch too


@pytest.mark.parametrize("text", ["0", "garbage", "20200101"])
def test_bad_or_past_days_are_refused(text):
    assert bot.decode_day(text) is None


def test_days_round_trip_and_stay_within_the_window():
    assert bot.decode_day(bot.encode_day(TODAY)) == TODAY
    assert bot.decode_day((TODAY + timedelta(days=bot.MAX_DAYS_AHEAD + 1)).strftime("%Y%m%d")) is None


# --- pressing them ---------------------------------------------------------------------

def _press(user, data):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    chat = SimpleNamespace(id=user.telegram_chat_id, send_message=AsyncMock(), send_document=AsyncMock())
    return SimpleNamespace(callback_query=query, effective_chat=chat), query


def _run(user, data):
    update, query = _press(user, data)
    asyncio.run(bot.on_workout_action(update, SimpleNamespace(user_data={})))
    return query


def test_save_button_plans_it(session, owner):
    row = stored(session, owner)
    day = TODAY + timedelta(days=2)
    query = _run(owner, f"wk:save:{row.id}:{day.strftime('%Y%m%d')}")

    session.refresh(row)
    assert (row.status, row.scheduled_for) == (STATUS_PLANNED, day)
    assert "שמור ל" in query.edit_message_text.call_args.args[0]


def test_send_button_saves_first_then_queues_the_upload(session, owner):
    row = stored(session, owner)
    with patch.object(tasks.send_workout_task, "delay") as queued:
        _run(owner, f"wk:send:{row.id}:{TODAY.strftime('%Y%m%d')}")

    queued.assert_called_once_with(owner.id, row.id, TODAY.isoformat())
    session.refresh(row)
    assert row.status == STATUS_PLANNED  # still in the week if Garmin says no


def test_send_button_is_refused_without_watch_access(session, make_user):
    me = make_user()
    row = stored(session, me)
    with patch.object(tasks.send_workout_task, "delay") as queued:
        query = _run(me, f"wk:send:{row.id}:{TODAY.strftime('%Y%m%d')}")
    queued.assert_not_called()
    assert query.message.reply_text.called


def test_buttons_on_someone_elses_workout_do_nothing(session, make_user):
    row = stored(session, make_user())
    stranger = make_user()
    query = _run(stranger, f"wk:save:{row.id}:{TODAY.strftime('%Y%m%d')}")
    session.refresh(row)
    assert row.status == STATUS_DRAFT and "לא קיים" in query.message.reply_text.call_args.args[0]


# --- the builder -------------------------------------------------------------------------

def test_every_builder_callback_fits_with_a_day():
    code = TODAY.strftime("%Y%m%d")
    assert set(bot.BUILDER_SPORTS) == set(bot.FOCUS)
    for sport in bot.BUILDER_SPORTS:
        amounts = [("t", bot.TIME_RANGE_MINUTES[1])]
        if sport in bot.DISTANCE_SPORTS:
            amounts.append(("d", bot.DISTANCE_RANGE_METRES[sport][1]))
        for mode, amount in amounts:
            for focus in bot.FOCUS[sport]:
                assert len(f"wkb:f:{sport}:{mode}:{amount}:{focus}:{code}".encode()) <= 64


def test_distance_only_where_it_makes_sense():
    assert bot.DISTANCE_SPORTS == set(bot.DISTANCE_RANGE_METRES) == {"running", "walking", "swimming", "cycling"}
    assert "✍️ זמן אחר" in labels(bot.amount_keyboard("yoga", "0"))
    assert bot.amount_text("running", "d", 5500) == '5.5 ק"מ'
    assert bot.amount_text("swimming", "d", 1500) == "1,500 מ'"


def _tap(data, user_data=None):
    query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=1))
    context = SimpleNamespace(user_data=user_data if user_data is not None else {})
    with patch.object(bot, "_require_active", AsyncMock(return_value=1)):
        asyncio.run(bot.on_builder_step(update, context))
    return context, query


@pytest.mark.parametrize("sport,unit", [("running", 'ק"מ'), ("swimming", "מטרים")])
def test_by_distance_goes_straight_to_typing(sport, unit):
    context, query = _tap(f"wkb:m:{sport}:d:0")
    assert context.user_data[bot.BUILDER_KEY] == {"sport": sport, "mode": "d", "day": "0"}
    assert unit in query.edit_message_text.call_args.args[0]
    assert "reply_markup" not in query.edit_message_text.call_args.kwargs  # no preset buttons


def test_no_distance_for_sports_without_one():
    context, query = _tap("wkb:m:yoga:d:0")
    assert context.user_data == {} and not query.edit_message_text.called


def test_the_coach_is_asked_for_the_amount_and_the_day():
    day = TODAY + timedelta(days=3)
    text = bot.builder_request("running", "d", 10000, "tempo", day)
    assert '10 ק"מ' in text and "טמפו" in text and f"date={day.isoformat()}" in text
    assert "45 דקות" in bot.builder_request("yoga", "t", 45, "general")


def test_free_text_carries_the_day_once():
    context = SimpleNamespace(user_data={bot.FREE_DAY_KEY: "2026-10-23"})
    assert "date=2026-10-23" in bot.with_free_day(context, "6x800")
    assert bot.with_free_day(context, "6x800") == "6x800"


def _type(state, text):
    context = SimpleNamespace(user_data={bot.BUILDER_KEY: state})
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    asyncio.run(bot.handle_builder_text(update, context, text))
    return context, update.message.reply_text.call_args


@pytest.mark.parametrize(
    "state,typed,expected",
    [
        ({"sport": "running", "mode": "t", "day": "0"}, "1:15", ":t:75:"),
        ({"sport": "running", "mode": "t", "day": "0"}, "75", ":t:75:"),
        ({"sport": "running", "mode": "d", "day": "0"}, "5.5", ":d:5500:"),
        ({"sport": "running", "mode": "d", "day": "0"}, '5.5 ק"מ', ":d:5500:"),
        ({"sport": "cycling", "mode": "d", "day": "0"}, "42", ":d:42000:"),
        ({"sport": "walking", "mode": "d", "day": "0"}, "6", ":d:6000:"),
        ({"sport": "swimming", "mode": "d", "day": "0"}, "1500", ":d:1500:"),
    ],
)
def test_a_typed_amount_leads_to_the_focus_question(state, typed, expected):
    context, call = _type(state, typed)
    assert bot.BUILDER_KEY not in context.user_data
    assert all(expected in b for b in buttons(call.kwargs["reply_markup"]) if b.startswith("wkb:f:"))


@pytest.mark.parametrize("typed", ["abc", "2", "900"])
def test_a_nonsense_amount_is_asked_again(typed):
    context, call = _type({"sport": "running", "mode": "t", "day": "0"}, typed)
    assert bot.BUILDER_KEY in context.user_data
    assert "לא הצלחתי" in call.args[0]


def test_a_typed_amount_never_reaches_the_coach():
    from telegram.ext import ApplicationHandlerStop

    from app.bot.manual_activity import on_manual_text

    context = SimpleNamespace(user_data={bot.BUILDER_KEY: {"sport": "running", "mode": "t", "day": "0"}})
    update = SimpleNamespace(
        message=SimpleNamespace(text="75", reply_text=AsyncMock()), effective_chat=SimpleNamespace(id=1)
    )
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(on_manual_text(update, context))
    assert bot.BUILDER_KEY not in context.user_data


def test_a_menu_tap_abandons_the_typed_amount():
    from app.bot.common import BTN_METRICS
    from app.bot.manual_activity import on_manual_text

    context = SimpleNamespace(user_data={bot.BUILDER_KEY: {"sport": "running", "mode": "t", "day": "0"}})
    update = SimpleNamespace(
        message=SimpleNamespace(text=BTN_METRICS, reply_text=AsyncMock()), effective_chat=SimpleNamespace(id=1)
    )
    asyncio.run(on_manual_text(update, context))  # returns quietly - the menu handler takes it
    assert bot.BUILDER_KEY not in context.user_data


def test_deleted_and_unsaved_workouts_are_not_in_the_week(session, make_user):
    me = make_user()
    deleted = stored(session, me)
    service.plan_workout(session, me.id, deleted.id, TODAY)
    service.discard_draft(session, me.id, deleted.id)
    stored(session, me, date=TODAY.isoformat())  # dated by the coach, never saved

    assert service.scheduled_between(session, me.id, TODAY, TODAY + timedelta(days=6)) == []
    assert format_upcoming_week(session, me.id) == ""
