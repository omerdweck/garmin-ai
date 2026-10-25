"""
A week built with the coach: the coach never ends a turn silent, build_week
saves a whole dated week in one call, the athlete reviews it workout by
workout (or all at once), and the Sunday-to-Saturday schedule shows what was
done next to what was planned.
"""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

import app.tasks as tasks
from app.bot import schedule as screen
from app.bot import workouts as bot
from app.core import claude_client
from app.core import clock
from app.core.claude_client import TOOLS, _execute_tool
from app.core.config import settings
from app.models.plan_session import week_index
from app.models.workout import STATUS_DRAFT, STATUS_PLANNED, STATUS_REMOVED, STATUS_SENT, StoredWorkout
from app.services import workouts as service
from app.services.metrics_view import workout_breakdown
from app.services.schedule import DONE, MISSED, PLANNED, week_schedule, week_start
from app.services.workout_schema import parse_workout
from tests.workout_examples import EXAMPLES


@pytest.fixture
def today():
    return clock.today()


@pytest.fixture
def owner(make_user, monkeypatch):
    user = make_user()
    monkeypatch.setattr(settings, "admin_chat_id", user.telegram_chat_id)
    return user


def dated(sport, day):
    return {**EXAMPLES[sport], "date": day.isoformat()}


def tool(session, user, name, payload):
    return json.loads(_execute_tool(session, user, name, payload))


def buttons(keyboard):
    return [b.callback_data for row in keyboard.inline_keyboard for b in row]


# --- the coach never ends a turn without a word -----------------------------------

class FakeClient:
    """Answers each messages.create with the next scripted response and records the calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def _usage():
    return SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def text(t, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=t)], usage=_usage())


def tool_call(name="get_training_goal"):
    block = SimpleNamespace(type="tool_use", name=name, input={}, id="t1")
    return SimpleNamespace(stop_reason="tool_use", content=[block], usage=_usage())


def silent(stop):
    return SimpleNamespace(stop_reason=stop, content=[], usage=_usage())


def run_turn(session, user, responses):
    client = FakeClient(responses)
    with patch.object(claude_client, "_client", return_value=client):
        reply, _ = claude_client.chat_with_coach(session, user, "תבנה לי שבוע")
    return reply, client.calls


def test_running_out_of_rounds_still_ends_with_an_answer(session, make_user):
    rounds = [tool_call() for _ in range(claude_client.MAX_TOOL_ROUNDS)]
    reply, calls = run_turn(session, make_user(), rounds + [text("בניתי לך שבוע")])
    assert reply == "בניתי לך שבוע"
    assert calls[-1]["tool_choice"] == {"type": "none"}
    assert calls[-1]["messages"][-1]["content"][0]["type"] == "tool_result"  # the last tools are answered


def test_an_answer_cut_off_by_length_is_asked_again_without_tools(session, make_user):
    reply, calls = run_turn(session, make_user(), [silent("max_tokens"), text("סיכום")])
    assert reply == "סיכום" and calls[-1]["tool_choice"] == {"type": "none"}


def test_an_empty_end_asks_for_a_wrap_up(session, make_user):
    reply, calls = run_turn(session, make_user(), [silent("end_turn"), text("הנה")])
    assert reply == "הנה"
    assert calls[-1]["messages"][-1] == {"role": "user", "content": claude_client.WRAP_UP_REQUEST}


def test_a_normal_answer_costs_no_extra_call(session, make_user):
    reply, calls = run_turn(session, make_user(), [text("שלום")])
    assert reply == "שלום" and len(calls) == 1
    assert calls[0]["max_tokens"] == claude_client.CHAT_MAX_TOKENS


# --- build_week ------------------------------------------------------------------------

def test_tools_offered_to_the_coach():
    names = {t["name"] for t in TOOLS}
    assert {"build_week", "get_training_schedule"} <= names
    assert "set_training_plan" not in names  # weeks are dated now
    json.dumps(TOOLS)


def test_a_whole_week_in_one_call(session, make_user, today):
    other, me = make_user(), make_user()
    week = [dated("strength", today + timedelta(days=1)), dated("running", today), dated("strength", today + timedelta(days=3))]
    result = tool(session, me, "build_week", {"workouts": week})

    assert result["saved"] and [w["date"] for w in result["week"]] == sorted(w["date"] for w in result["week"])
    rows = session.exec(select(StoredWorkout).where(StoredWorkout.user_id == me.id)).all()
    assert len(rows) == 3 and {r.proposal_id for r in rows} == {result["proposal_id"]}
    assert all(r.status == STATUS_DRAFT for r in rows)
    assert session.exec(select(StoredWorkout).where(StoredWorkout.user_id == other.id)).all() == []


@pytest.mark.parametrize("problem", ["no_date", "past", "far", "invalid"])
def test_one_bad_workout_saves_nothing(session, make_user, today, problem):
    me = make_user()
    bad = {
        "no_date": EXAMPLES["running"],
        "past": dated("running", today - timedelta(days=1)),
        "far": dated("running", today + timedelta(days=30)),
        "invalid": {**dated("running", today), "sport": "skydiving"},
    }[problem]
    result = tool(session, me, "build_week", {"workouts": [dated("yoga", today), bad]})

    assert result["saved"] is False and any("workout 2" in e for e in result["errors"])
    assert session.exec(select(StoredWorkout)).all() == []


def test_a_revised_week_replaces_the_one_waiting_for_review(session, make_user, today):
    me = make_user()
    first = tool(session, me, "build_week", {"workouts": [dated("running", today), dated("yoga", today)]})
    kept_id = first["week"][0]["workout_id"]
    service.plan_workout(session, me.id, kept_id, today)  # the athlete already kept one

    second = tool(session, me, "build_week", {"workouts": [dated("cycling", today)]})

    statuses = {r.id: r.status for r in session.exec(select(StoredWorkout)).all()}
    assert statuses[kept_id] == STATUS_PLANNED  # what was kept stays
    assert statuses[first["week"][1]["workout_id"]] == STATUS_REMOVED
    assert statuses[second["week"][0]["workout_id"]] == STATUS_DRAFT


# --- changing an existing workout ------------------------------------------------------------

def _kept(session, user, sport="running", day=None, status=STATUS_PLANNED):
    workout, _ = parse_workout(EXAMPLES[sport])
    row = service.save_workout(session, user.id, workout)
    row.status, row.scheduled_for = status, day or clock.today()
    session.add(row)
    session.commit()
    return row


def test_a_new_version_takes_the_old_ones_day(session, make_user, today):
    me = make_user()
    old = _kept(session, me, day=today + timedelta(days=2))
    result = tool(session, me, "build_workout", {**EXAMPLES["running"], "replaces_workout_id": old.id})
    new = session.get(StoredWorkout, result["workout_id"])
    assert new.replaces_workout_id == old.id and new.scheduled_for == old.scheduled_for


def test_nobody_can_replace_another_users_workout(session, make_user):
    theirs = _kept(session, make_user())
    me = make_user()
    result = tool(session, me, "build_workout", {**EXAMPLES["running"], "replaces_workout_id": theirs.id})
    assert session.get(StoredWorkout, result["workout_id"]).replaces_workout_id is None


def test_keeping_the_new_version_removes_the_old_one(session, make_user):
    me = make_user()
    old = _kept(session, me)
    new = _kept(session, me, status=STATUS_DRAFT)
    new.replaces_workout_id = old.id
    assert service.take_over_from_old(session, me.id, new) is None
    assert session.get(StoredWorkout, old.id).status == STATUS_REMOVED


def test_an_old_version_on_the_watch_is_handed_back_for_removal(session, make_user):
    me = make_user()
    old = _kept(session, me, status=STATUS_SENT)
    new = _kept(session, me, status=STATUS_DRAFT)
    new.replaces_workout_id = old.id
    assert service.take_over_from_old(session, me.id, new) == old.id
    assert new.replaces_workout_id is None


# --- the buttons: delete, edit -----------------------------------------------------------------

def _press(user, data, user_data=None):
    query = SimpleNamespace(
        data=data, answer=AsyncMock(), edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    chat = SimpleNamespace(id=user.telegram_chat_id, send_message=AsyncMock(), send_document=AsyncMock())
    context = SimpleNamespace(user_data=user_data if user_data is not None else {})
    return SimpleNamespace(callback_query=query, effective_chat=chat), query, context


def press_workout(user, data, user_data=None):
    update, query, context = _press(user, data, user_data)
    asyncio.run(bot.on_workout_action(update, context))
    return query, context


def press_review(user, data, user_data=None):
    update, query, context = _press(user, data, user_data)
    asyncio.run(screen.on_week_review(update, context))
    return query, context


def test_deleting_a_kept_workout_asks_first(session, owner):
    row = _kept(session, owner, status=STATUS_SENT)
    query, _ = press_workout(owner, f"wk:del:{row.id}")
    confirm = query.edit_message_reply_markup.call_args.kwargs["reply_markup"]
    assert buttons(confirm) == [f"wk:drop:{row.id}", f"wk:back:{row.id}"]
    assert session.get(StoredWorkout, row.id).status == STATUS_SENT  # nothing deleted yet


def test_confirmed_delete_of_a_watch_workout_removes_it_from_garmin(session, owner):
    row = _kept(session, owner, status=STATUS_SENT)
    with patch.object(tasks.remove_workout_task, "delay") as removal:
        press_workout(owner, f"wk:drop:{row.id}")
    removal.assert_called_once_with(owner.id, row.id)


def test_kept_workouts_offer_edit_and_a_guarded_delete(session, owner):
    row = _kept(session, owner)
    _, workout = service.load_workout(session, owner.id, row.id)
    keys = buttons(bot.workout_keyboard(row, workout, owner, ftp=None))
    assert f"wk:edit:{row.id}" in keys and f"wk:del:{row.id}" in keys and f"wk:drop:{row.id}" not in keys


def test_edit_hands_the_next_message_to_the_coach_with_the_workout(session, owner):
    row = _kept(session, owner)
    _, context = press_workout(owner, f"wk:edit:{row.id}")
    assert context.user_data[bot.EDIT_KEY] == row.id
    note = bot.with_free_day(context, "תקצר ל-40 דקות")
    assert f"replaces_workout_id={row.id}" in note and bot.EDIT_KEY not in context.user_data


def test_saving_a_new_version_of_a_watch_workout_removes_the_old_from_the_watch(session, owner, today):
    old = _kept(session, owner, status=STATUS_SENT)
    new = _kept(session, owner, status=STATUS_DRAFT)
    new.replaces_workout_id = old.id
    session.add(new)
    session.commit()
    with patch.object(tasks.remove_workout_task, "delay") as removal:
        press_workout(owner, f"wk:save:{new.id}:{today.strftime('%Y%m%d')}")
    removal.assert_called_once_with(owner.id, old.id, True)


# --- reviewing a proposed week -----------------------------------------------------------------

@pytest.fixture
def proposal(session, owner, today):
    result = tool(session, owner, "build_week", {"workouts": [
        dated("strength", today), dated("running", today + timedelta(days=1)), dated("yoga", today + timedelta(days=2)),
    ]})
    return result["proposal_id"], [w["workout_id"] for w in result["week"]]


def statuses(session, ids):
    return [session.get(StoredWorkout, i).status for i in ids]


def test_review_one_by_one(session, owner, proposal, today):
    pid, ids = proposal
    with patch.object(tasks.send_workout_task, "apply_async") as sent:
        press_review(owner, f"wkw:sw:{pid}:0")
        press_review(owner, f"wkw:kp:{pid}:1")
        query, _ = press_review(owner, f"wkw:sk:{pid}:2")

    assert statuses(session, ids) == [STATUS_PLANNED, STATUS_PLANNED, STATUS_REMOVED]
    sent.assert_called_once()
    assert sent.call_args.kwargs["args"] == [owner.id, ids[0], today.isoformat()]
    assert "סיימנו" in query.edit_message_text.call_args.args[0]  # the last one ends the review


def test_positions_stay_put_after_a_skip(session, owner, proposal):
    pid, ids = proposal
    press_review(owner, f"wkw:sk:{pid}:0")
    query, _ = press_review(owner, f"wkw:r:{pid}:1")
    assert "אימון 2 מתוך 3" in query.edit_message_text.call_args.args[0]


def test_everything_to_the_watch_at_once_spread_out(session, owner, proposal):
    pid, ids = proposal
    with patch.object(tasks.send_workout_task, "apply_async") as sent:
        press_review(owner, f"wkw:all:{pid}")
    assert statuses(session, ids) == [STATUS_PLANNED] * 3
    assert [c.kwargs["countdown"] for c in sent.call_args_list] == [0, 3, 6]


def test_all_to_the_watch_only_sends_open_sports(session, make_user, today):
    me = make_user()
    pid = tool(session, me, "build_week", {"workouts": [dated("running", today)]})["proposal_id"]
    with patch.object(tasks.send_workout_task, "apply_async") as sent:
        press_review(me, f"wkw:all:{pid}")
    sent.assert_not_called()  # saved in the bot instead


def test_keep_all_and_drop_all(session, owner, proposal):
    pid, ids = proposal
    press_review(owner, f"wkw:drop:{pid}")
    assert statuses(session, ids) == [STATUS_REMOVED] * 3


def test_someone_elses_proposal_is_unknown(session, owner, proposal, make_user):
    pid, ids = proposal
    stranger = make_user()
    query, _ = press_review(stranger, f"wkw:all:{pid}")
    assert statuses(session, ids) == [STATUS_DRAFT] * 3
    assert "לא קיימת" in query.message.reply_text.call_args.args[0]


def test_proposal_workouts_are_not_also_shown_one_by_one(session, owner, proposal):
    update = SimpleNamespace(effective_chat=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(bot.show_new_workouts(update, owner.telegram_chat_id, since=datetime(2000, 1, 1)))
    update.effective_chat.send_message.assert_not_called()


# --- the Sunday-to-Saturday schedule ---------------------------------------------------------------

def _activity(make_activity, user, day, activity_type="running"):
    return make_activity(user, activity_type=activity_type, start_time=datetime.combine(day, datetime.min.time()).replace(hour=7))


def test_week_runs_sunday_to_saturday(session, make_user, today):
    week = week_schedule(session, make_user().id)
    assert week[0].day == week_start(today) and week_index(week[0].day) == 0 and len(week) == 7


def test_done_planned_and_missed(session, make_user, make_activity, today):
    me = make_user()
    sunday = week_start(today)
    done_day, missed_day = sunday, sunday + timedelta(days=1)
    if today <= missed_day:  # on Sunday/Monday look at last week, where both days are over
        offset, done_day, missed_day = -1, sunday - timedelta(days=7), sunday - timedelta(days=6)
    else:
        offset = 0
    _kept(session, me, "running", day=done_day)
    _activity(make_activity, me, done_day, "treadmill_running")  # a treadmill run is a run
    _kept(session, me, "yoga", day=missed_day)
    _activity(make_activity, me, missed_day, "lap_swimming")  # done, but not what was planned

    week = {d.day: d for d in week_schedule(session, me.id, offset)}
    assert [state for _, state in week[done_day].planned] == [DONE]
    assert [state for _, state in week[missed_day].planned] == [MISSED]
    assert len(week[missed_day].done) == 1  # the unplanned swim still shows

    text = screen.format_schedule(list(week.values()))
    assert text.count("✅") == 2 + 1  # done run, the swim, and the total line


def test_future_planned_and_other_users_hidden(session, make_user, make_activity, today):
    me, other = make_user(), make_user()
    _kept(session, me, "cycling", day=today)
    _kept(session, other, "yoga", day=today)
    _activity(make_activity, other, today)
    day = next(d for d in week_schedule(session, me.id) if d.day == today)
    assert [(w.sport, s) for w, s in day.planned] == [("cycling", PLANNED)] and day.done == []


def test_schedule_buttons(session, make_user, today):
    me = make_user()
    row = _kept(session, me, day=today)
    week = week_schedule(session, me.id)
    keys = buttons(screen.schedule_keyboard(week, 0, has_template=False))
    adds = [k for k in keys if k.startswith("wkb:day:")]
    assert adds and all(k[-8:] >= today.strftime("%Y%m%d") for k in adds)  # ➕ only from today on
    assert {"sch:-1", "sch:1", f"wk:open:{row.id}", "plan:new"} <= set(keys)
    assert "plan:delete" not in keys and "sch:0" not in keys

    last = screen.schedule_keyboard(week_schedule(session, me.id, screen.NEWEST_WEEK), screen.NEWEST_WEEK, True)
    assert f"sch:{screen.NEWEST_WEEK + 1}" not in buttons(last) and "plan:delete" in buttons(last)


def test_a_past_week_has_no_add_buttons(session, make_user):
    week = week_schedule(session, make_user().id, -1)
    assert not any(k.startswith("wkb:day:") for k in buttons(screen.schedule_keyboard(week, -1, False)))


# --- the weekly summary ---------------------------------------------------------------------------

def test_workouts_counted_by_type(make_user, make_activity, session):
    me = make_user()
    for kind in ("running", "treadmill_running", "trail_running", "lap_swimming"):
        make_activity(me, activity_type=kind)
    from app.models.activity import Activity

    activities = session.exec(select(Activity).where(Activity.user_id == me.id)).all()
    assert workout_breakdown(activities) == "3 אימוני ריצה, אימון שחייה אחד"


def test_old_button_labels_still_work():
    from app.bot.common import LEGACY_BTN_PLAN, LEGACY_BTN_WEEK, MENU_BUTTONS

    assert {LEGACY_BTN_PLAN, LEGACY_BTN_WEEK} <= MENU_BUTTONS


def test_a_last_round_with_only_a_progress_note_still_gets_the_real_answer(session, make_user):
    note = SimpleNamespace(type="text", text="רגע, בודק...")
    calling = SimpleNamespace(type="tool_use", name="get_training_goal", input={}, id="t1")
    rounds = [SimpleNamespace(stop_reason="tool_use", content=[note, calling], usage=_usage())] * claude_client.MAX_TOOL_ROUNDS
    reply, calls = run_turn(session, make_user(), rounds + [text("הנה השבוע")])
    assert reply == "הנה השבוע" and calls[-1]["tool_choice"] == {"type": "none"}


def test_skipped_workouts_leave_the_proposal_summary(session, owner, proposal):
    pid, ids = proposal
    press_review(owner, f"wkw:sk:{pid}:0")
    rows = service.proposal_rows(session, owner.id, pid)
    assert [r.id for r in rows] == ids[1:]
    assert "2 אימונים" in screen.proposal_summary(rows)


def test_done_workouts_have_no_open_button(session, make_user, make_activity, today):
    me = make_user()
    row = _kept(session, me, "running", day=today)
    _activity(make_activity, me, today)
    keys = buttons(screen.schedule_keyboard(week_schedule(session, me.id), 0, False))
    assert f"wk:open:{row.id}" not in keys
