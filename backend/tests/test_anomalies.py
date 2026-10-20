"""
Anomaly episodes end to end, on a real database: baselines from the
athlete's own history, one announcement per episode, several rules in one
message, and nothing that can flood or leak.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlmodel import select

import app.tasks as tasks
from app.core import clock
from app.core.claude_client import ClaudeUnavailableError
from app.models.coach_event import KIND_ANOMALY, CoachEvent
from app.models.daily_metric import DailyMetric
from app.models.metric_alert import MetricAlert, MetricBaseline
from app.services import anomalies
from app.services import anomaly_rules as r
from app.services.anomaly_message import FOOTER, HEADER, compose_anomaly

END = clock.today() - timedelta(days=20)  # leaves room for "later days" in the tests

NORMAL = {
    "resting_heart_rate": (50, 52),
    "avg_hrv": (52, 56),
    "sleep_duration_minutes": (440, 460),
    "sleep_score": (78, 82),
    "body_battery_high": (80, 90),
}


def put_day(session, user, day, **values):
    row = session.exec(select(DailyMetric).where(DailyMetric.user_id == user.id, DailyMetric.date == day)).first()
    row = row or DailyMetric(user_id=user.id, date=day)
    for key, value in values.items():
        setattr(row, key, value)
    session.add(row)
    session.commit()


@pytest.fixture
def athlete(session, make_user):
    """Five weeks of ordinary days ending on END."""
    user = make_user()
    for offset in range(35):
        day = END - timedelta(days=offset)
        put_day(session, user, day, **{k: v[offset % 2] for k, v in NORMAL.items()})
    return user


def run(session, user, end=END):
    anomalies.update_episodes(session, user.id, anomalies.evaluate(session, user.id, end), end)
    return anomalies.announce(session, user.id, end)


def open_alerts(session, user):
    return session.exec(
        select(MetricAlert).where(MetricAlert.user_id == user.id, MetricAlert.ended_on.is_(None))
    ).all()


def high_rhr(session, user, *days):
    for day in days:
        put_day(session, user, day, resting_heart_rate=59)


# --- baseline and detection ----------------------------------------------

def test_ordinary_weeks_raise_nothing(session, athlete):
    assert run(session, athlete) is None
    assert open_alerts(session, athlete) == []


def test_baselines_are_stored_from_the_weeks_before(session, athlete):
    run(session, athlete)
    rhr = session.exec(
        select(MetricBaseline).where(
            MetricBaseline.user_id == athlete.id, MetricBaseline.metric == "resting_heart_rate"
        )
    ).one()
    assert (rhr.mean, rhr.samples) == (51, 28)
    assert rhr.window_end == END - timedelta(days=4)  # the judged days are left out


def test_new_user_without_history_gets_no_alerts(session, make_user):
    newbie = make_user()
    for offset in range(3):
        put_day(session, newbie, END - timedelta(days=offset), resting_heart_rate=70, body_battery_high=90)
    findings = anomalies.evaluate(session, newbie.id, END)
    assert findings[r.RULE_RHR].status == r.UNKNOWN


def test_a_real_change_opens_an_episode_and_announces_it(session, athlete):
    high_rhr(session, athlete, END - timedelta(days=1), END)

    event = run(session, athlete)

    [alert] = open_alerts(session, athlete)
    assert alert.rule == r.RULE_RHR and alert.details["baseline"] == 51
    assert event.kind == KIND_ANOMALY and event.payload == {"alert_ids": [alert.id]}
    assert alert.event_id == event.id


def test_an_episode_is_announced_once_however_long_it_lasts(session, athlete):
    for offset in range(-1, 6):
        high_rhr(session, athlete, END + timedelta(days=offset))
    events = [run(session, athlete, END + timedelta(days=d)) for d in range(5)]

    assert events[0] is not None and events[1:] == [None] * 4
    assert len(open_alerts(session, athlete)) == 1


def test_several_rules_together_are_one_message(session, athlete):
    high_rhr(session, athlete, END - timedelta(days=1), END)
    for offset in range(3):
        put_day(session, athlete, END - timedelta(days=offset), body_battery_high=40)

    event = run(session, athlete)

    assert len(event.payload["alert_ids"]) == 2


def test_a_second_rule_later_that_day_waits_for_tomorrow(session, athlete):
    high_rhr(session, athlete, END - timedelta(days=1), END)
    run(session, athlete)
    for offset in range(4):
        put_day(session, athlete, END - timedelta(days=offset), body_battery_high=40)

    assert run(session, athlete) is None  # today's message is used
    high_rhr(session, athlete, END + timedelta(days=1))
    put_day(session, athlete, END + timedelta(days=1), body_battery_high=40)
    tomorrow = run(session, athlete, END + timedelta(days=1))

    [battery] = [a for a in open_alerts(session, athlete) if a.rule == r.RULE_BODY_BATTERY]
    assert tomorrow.payload == {"alert_ids": [battery.id]}


def test_missing_days_do_not_end_an_episode(session, athlete):
    high_rhr(session, athlete, END - timedelta(days=1), END)
    run(session, athlete)
    # Watch in a drawer for three days: no RHR at all.
    for offset in range(1, 4):
        put_day(session, athlete, END + timedelta(days=offset), resting_heart_rate=None)
    run(session, athlete, END + timedelta(days=3))

    assert len(open_alerts(session, athlete)) == 1


def test_an_episode_without_data_for_a_week_is_closed(session, athlete):
    high_rhr(session, athlete, END - timedelta(days=1), END)
    run(session, athlete)
    for offset in range(1, 10):
        put_day(session, athlete, END + timedelta(days=offset), resting_heart_rate=None)
    run(session, athlete, END + timedelta(days=9))

    assert open_alerts(session, athlete) == []


def _back_to_normal(session, user, day):
    for offset in range(3):
        put_day(session, user, day - timedelta(days=offset), resting_heart_rate=51)


def test_coming_back_soon_continues_the_same_episode(session, athlete):
    high_rhr(session, athlete, END - timedelta(days=1), END)
    first = run(session, athlete)
    _back_to_normal(session, athlete, END + timedelta(days=3))
    run(session, athlete, END + timedelta(days=3))
    assert open_alerts(session, athlete) == []

    high_rhr(session, athlete, END + timedelta(days=4), END + timedelta(days=5))
    again = run(session, athlete, END + timedelta(days=5))

    assert again is None  # hovering at the line is not news
    [alert] = open_alerts(session, athlete)
    assert alert.event_id == first.id


def test_a_new_episode_after_a_real_break_is_announced(session, athlete):
    high_rhr(session, athlete, END - timedelta(days=1), END)
    run(session, athlete)
    _back_to_normal(session, athlete, END + timedelta(days=3))
    run(session, athlete, END + timedelta(days=3))

    later = END + timedelta(days=10)
    for offset in range(3, 10):
        put_day(session, athlete, END + timedelta(days=offset), resting_heart_rate=51)
    high_rhr(session, athlete, later - timedelta(days=1), later)

    assert run(session, athlete, later) is not None


def test_one_open_episode_per_rule_is_enforced_by_the_database(session, athlete):
    from sqlalchemy.exc import IntegrityError

    session.add(MetricAlert(user_id=athlete.id, rule=r.RULE_RHR, started_on=END, last_seen_on=END))
    session.commit()
    session.add(MetricAlert(user_id=athlete.id, rule=r.RULE_RHR, started_on=END, last_seen_on=END))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- easy runs and volume, from stored workouts --------------------------

def _run_at(make_activity, user, day, pace_s_per_km, hr, km=8.0):
    return make_activity(
        user, activity_type="running", distance_meters=km * 1000, duration_seconds=pace_s_per_km * km,
        avg_heart_rate=hr, start_time=datetime.combine(day, datetime.min.time()).replace(hour=7),
    )


def test_only_runs_slower_than_the_athletes_median_count_as_easy(session, make_user, make_activity):
    me = make_user()
    _run_at(make_activity, me, END - timedelta(days=5), 300, 165)  # 5:00/km - tempo
    _run_at(make_activity, me, END - timedelta(days=4), 360, 140)  # 6:00/km - easy
    _run_at(make_activity, me, END - timedelta(days=3), 370, 138)  # 6:10/km - easy

    runs = anomalies.easy_runs(session, me.id, END)

    assert [day for day, _ in runs] == [END - timedelta(days=4), END - timedelta(days=3)]


def test_easy_runs_with_a_working_heart_fire(session, athlete, make_activity):
    for week in range(8, 2, -1):  # six weeks of steady easy running
        for day in (1, 3):
            _run_at(make_activity, athlete, END - timedelta(weeks=week, days=day), 360, 140)
        _run_at(make_activity, athlete, END - timedelta(weeks=week, days=5), 290, 168)  # weekly tempo
    for offset in (9, 5, 1):
        _run_at(make_activity, athlete, END - timedelta(days=offset), 360, 152)  # same pace, +12 bpm

    finding = anomalies.evaluate(session, athlete.id, END)[r.RULE_EASY_RUN]

    assert finding.status == r.FIRING and finding.details["drop_pct"] > 7


def test_a_week_with_a_new_watch_does_not_look_like_a_volume_jump(session, make_user, make_activity):
    # History starts 10 days ago. The "four weeks before" hold only three
    # days of it - divided by four they would look like a light month, and
    # this ordinary week like a 100% jump.
    me = make_user()
    put_day(session, me, END - timedelta(days=10), steps=1)
    for offset in (10, 9, 8):
        _run_at(make_activity, me, END - timedelta(days=offset), 360, 140, km=20)  # 2h each
    for offset in (5, 3, 1):
        _run_at(make_activity, me, END - timedelta(days=offset), 360, 140, km=20)

    assert anomalies.evaluate(session, me.id, END)[r.RULE_LOAD].status == r.UNKNOWN


# --- the message ------------------------------------------------------------

@pytest.fixture
def announced(session, athlete):
    high_rhr(session, athlete, END - timedelta(days=1), END)
    return run(session, athlete)


def test_message_has_numbers_note_and_disclaimer(session, athlete, announced):
    with patch("app.services.anomaly_message.generate_anomaly_note", return_value="כדאי להקל מחר."):
        text = compose_anomaly(session, athlete, announced)

    assert text.startswith(HEADER)
    assert "59" in text and "~51" in text
    assert "כדאי להקל מחר." in text
    assert text.endswith(FOOTER)


def test_message_goes_out_with_a_fixed_hint_when_the_ai_is_down(session, athlete, announced):
    with patch("app.services.anomaly_message.generate_anomaly_note", side_effect=ClaudeUnavailableError("x")):
        text = compose_anomaly(session, athlete, announced)

    assert "יום קל" in text and text.endswith(FOOTER)


def test_another_users_alert_is_never_described(session, athlete, announced, make_user):
    stranger = make_user()
    assert compose_anomaly(session, stranger, announced) is None


def test_the_ai_gets_upcoming_planned_sessions(session, athlete, announced):
    from app.models.plan_session import PlanSession, week_index
    from app.models.training_plan import TrainingPlan

    plan = TrainingPlan(user_id=athlete.id, discipline="running", plan="p", is_active=True)
    session.add(plan)
    session.commit()
    tomorrow = clock.today() + timedelta(days=1)
    session.add(PlanSession(user_id=athlete.id, plan_id=plan.id, day_of_week=week_index(tomorrow), title="אינטרוולים"))
    session.commit()

    with patch("app.services.anomaly_message.generate_anomaly_note", return_value="x") as ai:
        compose_anomaly(session, athlete, announced)

    facts = ai.call_args.args[2]
    assert {"date": tomorrow.isoformat(), "title": "אינטרוולים"}.items() <= facts["upcoming_sessions"][0].items()


# --- inside the sync ---------------------------------------------------------

@pytest.fixture
def quiet_sync():
    with patch.object(tasks, "sync_user_garmin_data", return_value=[]), \
         patch.object(tasks, "send_telegram_message"), \
         patch.object(tasks, "_warn_if_watch_stale"), \
         patch.object(tasks.deliver_coach_event_task, "delay") as delivered:
        yield delivered


def test_the_sync_delivers_a_new_alert(session, make_user, quiet_sync):
    me = make_user()
    fake_event = CoachEvent(id=4242, user_id=me.id, kind=KIND_ANOMALY, key="k")
    with patch.object(tasks.anomalies, "check_user", return_value=fake_event):
        tasks._sync_one_user(tasks.sync_one_user_task, me.id)

    quiet_sync.assert_called_once_with(4242)


def test_a_broken_anomaly_check_does_not_break_the_sync(session, make_user, quiet_sync):
    me = make_user()
    with patch.object(tasks.anomalies, "check_user", side_effect=RuntimeError("bug")), \
         patch.object(tasks, "_warn_if_watch_stale") as stale_check:
        tasks._sync_one_user(tasks.sync_one_user_task, me.id)

    stale_check.assert_called_once()  # the rest of the sync still ran
