"""
The home screen's additions to /api/today: the activity rings and their
goals, the load balance, the coach line, and the week strip.

TODAY is a Monday, so "this week" is Sunday the 25th and today.
"""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.security import create_miniapp_token
from app.db.session import get_session
from app.models.daily_metric import DailyMetric
from app.models.workout import STATUS_PLANNED, STATUS_SENT, StoredWorkout
from app.services import dashboard_stats as ds
from app.services.garmin_sync import _apply_daily_metric
from app.webapp import app

TODAY = date(2026, 10, 26)
SUNDAY = date(2026, 10, 25)


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setattr("app.routers.miniapp.clock.today", lambda: TODAY)
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def get_today(client, user) -> dict:
    return client.get("/api/today", headers={"Authorization": f"Bearer {create_miniapp_token(user.id)}"}).json()


def day(session, user, d, **fields):
    session.add(DailyMetric(user_id=user.id, date=d, **fields))
    session.commit()


def at(d: date, hour: int = 7) -> datetime:
    return datetime(d.year, d.month, d.day, hour)


# --- goals --------------------------------------------------------------------


def test_goals_fall_back_to_defaults_without_any_garmin_goal(client, session, make_user):
    user = make_user()
    day(session, user, TODAY, steps=4000)
    rings = get_today(client, user)["activity_rings"]
    assert rings["day"] == {"steps": 4000, "step_goal": ds.DEFAULT_STEP_GOAL}
    assert rings["week"]["step_goal"] == 7 * ds.DEFAULT_STEP_GOAL
    assert rings["week"]["intensity_goal"] == ds.DEFAULT_INTENSITY_GOAL


def test_a_day_without_a_goal_takes_the_latest_known_one(client, session, make_user):
    user = make_user()
    day(session, user, SUNDAY - timedelta(days=10), daily_step_goal=9000, intensity_minutes_goal=200)
    day(session, user, TODAY, steps=3000)  # synced, but Garmin sent no goal
    rings = get_today(client, user)["activity_rings"]
    assert rings["day"]["step_goal"] == 9000
    assert rings["week"]["intensity_goal"] == 200


def test_week_step_goal_is_the_sum_of_daily_goals(client, session, make_user):
    user = make_user()
    day(session, user, SUNDAY, steps=10_000, daily_step_goal=8000)
    day(session, user, TODAY, steps=2000, daily_step_goal=8500)
    week = get_today(client, user)["activity_rings"]["week"]
    assert week["steps"] == 12_000
    # Sunday's own goal, today's own goal, and today's (the latest) for the five days still ahead.
    assert week["step_goal"] == 8000 + 8500 + 5 * 8500


def test_intensity_minutes_count_vigorous_twice(client, session, make_user):
    user = make_user()
    day(session, user, SUNDAY - timedelta(days=1), moderate_intensity_minutes=100)  # last week
    day(session, user, SUNDAY, moderate_intensity_minutes=20, vigorous_intensity_minutes=10)
    day(session, user, TODAY, moderate_intensity_minutes=5)
    assert get_today(client, user)["activity_rings"]["week"]["intensity_minutes"] == 20 + 2 * 10 + 5


def test_sync_stores_goals_and_keeps_them_when_garmin_sends_none(session, make_user):
    user = make_user()

    def sync(summary):
        payload = {"summary": summary, "sleep": {}, "hrv": {}, "max_metrics": [], "training": {}}
        _apply_daily_metric(session, user.id, TODAY, payload)
        session.commit()

    sync({"totalSteps": 100, "dailyStepGoal": 8240, "intensityMinutesGoal": 150})
    sync({"totalSteps": 200})  # a later sync without the goals
    stored = session.exec(select(DailyMetric).where(DailyMetric.user_id == user.id)).one()
    assert (stored.steps, stored.daily_step_goal, stored.intensity_minutes_goal) == (200, 8240, 150)


# --- the workouts ring ----------------------------------------------------------


def test_workouts_goal_defaults_to_three_without_history(session, make_user, make_activity):
    user = make_user()
    assert ds.workouts_goal(session, user.id, TODAY) == 3
    # Only this week's workouts: still no full week to average.
    make_activity(user, start_time=at(SUNDAY))
    assert ds.workouts_goal(session, user.id, TODAY) == 3


def test_workouts_goal_averages_eight_full_weeks(session, make_user, make_activity):
    user = make_user()
    # 9 and 10 weeks ago: outside the window, must not count.
    for weeks_ago in (9, 10):
        for _ in range(7):
            make_activity(user, start_time=at(SUNDAY - timedelta(weeks=weeks_ago)))
    # The 8 weeks before this one: 4 workouts each, plus a walk each that doesn't count.
    for weeks_ago in range(1, 9):
        for i in range(4):
            make_activity(user, start_time=at(SUNDAY - timedelta(weeks=weeks_ago) + timedelta(days=i)))
        make_activity(user, activity_type="walking", start_time=at(SUNDAY - timedelta(weeks=weeks_ago)))
    # This week doesn't count toward the average either.
    for _ in range(6):
        make_activity(user, start_time=at(SUNDAY))
    assert ds.workouts_goal(session, user.id, TODAY) == 4


def test_workouts_goal_uses_the_weeks_that_exist(session, make_user, make_activity):
    user = make_user()
    # Two full weeks of history: 3 and 2 workouts -> 2.5, rounded half up.
    for i in range(3):
        make_activity(user, start_time=at(SUNDAY - timedelta(weeks=2) + timedelta(days=i)))
    for i in range(2):
        make_activity(user, start_time=at(SUNDAY - timedelta(weeks=1) + timedelta(days=i)))
    assert ds.workouts_goal(session, user.id, TODAY) == 3


def test_workouts_goal_is_at_least_one(session, make_user, make_activity):
    user = make_user()
    # Eight weeks, a single workout and some walks: the mean rounds to 0.
    make_activity(user, start_time=at(SUNDAY - timedelta(weeks=8)))
    for weeks_ago in range(1, 8):
        make_activity(user, activity_type="walking", start_time=at(SUNDAY - timedelta(weeks=weeks_ago)))
    assert ds.workouts_goal(session, user.id, TODAY) == 1


def test_walks_do_not_count_as_workouts_this_week(client, session, make_user, make_activity):
    user = make_user()
    make_activity(user, start_time=at(SUNDAY))
    make_activity(user, activity_type="walking", start_time=at(TODAY))
    make_activity(user, activity_type="hiking", start_time=at(TODAY, 9))
    assert get_today(client, user)["activity_rings"]["week"]["workouts"] == 1


# --- load balance ---------------------------------------------------------------


@pytest.mark.parametrize("form, zone", [
    (-45, "high"), (-30.1, "high"), (-30, "building"), (-10.1, "building"),
    (-10, "balanced"), (0, "balanced"), (5, "balanced"), (5.1, "fresh"), (20, "fresh"),
])
def test_load_zone_thresholds(form, zone):
    assert ds.load_zone(form) == zone


def test_load_balance_in_today(client, session, make_user, make_activity):
    user = make_user()
    assert get_today(client, user)["load_balance"] is None  # no workouts, no load
    # A big load yesterday: fatigue jumps far above fitness.
    make_activity(user, start_time=at(SUNDAY), raw_json={"activityTrainingLoad": 400})
    balance = get_today(client, user)["load_balance"]
    assert balance["form"] < -30 and balance["zone"] == "high"
    assert balance["word"] and balance["advice"]


# --- coach line -----------------------------------------------------------------


def ring(state):
    return {"state": state}


def test_coach_line_good_signals_and_intervals():
    planned = [{"sport": "running", "blocks": [{"kind": "warmup"}, {"kind": "interval"}]}]
    line = ds.coach_line({"hrv": ring("good"), "rhr": ring("normal")}, 450, 80, "balanced", planned, [])
    assert line == "ה-HRV מעל הרגיל וישנת 7.5 שעות, יום טוב לאינטרוולים"


def test_coach_line_high_resting_hr_means_an_easy_day():
    line = ds.coach_line({"hrv": ring("normal"), "rhr": ring("low")}, 400, 60, "balanced", [], [])
    assert line == "דופק המנוחה גבוה מהרגיל, כדאי יום קל"


def test_coach_line_bad_news_wins_over_good():
    line = ds.coach_line({"hrv": ring("good"), "rhr": ring("normal")}, 300, 60, "high", [], [])
    assert line.startswith("ישנת רק 5 שעות והעומס המצטבר גבוה")


def test_coach_line_after_a_workout_today():
    line = ds.coach_line({"hrv": ring("low")}, None, None, None, [], [{"name": "ריצת טמפו"}])
    assert line.startswith("ריצת טמפו כבר מאחוריך")


def test_coach_line_without_any_data():
    assert ds.coach_line({"hrv": ring("unknown")}, None, None, None, [], []) is None


# --- the rest of the payload ----------------------------------------------------


def test_today_payload_for_the_home_screen(client, session, make_user, make_activity):
    user = make_user()
    day(session, user, TODAY, sleep_duration_minutes=470, avg_hrv=60)
    done = make_activity(user, activity_name="ריצה קלה", start_time=at(TODAY), duration_seconds=1800.4,
                         distance_meters=5012.0)
    make_activity(user, activity_name="אתמול", start_time=at(SUNDAY))
    session.add(StoredWorkout(user_id=user.id, sport="cycling", name="רכיבה", status=STATUS_PLANNED,
                              scheduled_for=TODAY + timedelta(days=3), spec={}))
    session.add(StoredWorkout(user_id=user.id, sport="running", name="ארוכה", status=STATUS_SENT,
                              scheduled_for=TODAY + timedelta(days=5), spec={}))
    session.commit()

    body = get_today(client, user)
    assert body["today_done"] == [
        {"id": done.id, "name": "ריצה קלה", "sport": "running", "seconds": 1800, "meters": 5012}
    ]
    assert body["next_workout"] == {"name": "רכיבה", "sport": "cycling", "date": "2026-10-29", "on_watch": False}
    assert [d["date"] for d in body["week"]][0] == SUNDAY.isoformat() and len(body["week"]) == 7
    assert len(body["week"][0]["done"]) == 1 and body["week"][4]["planned"][0]["name"] == "רכיבה"
    assert body["coach_line"].startswith("ריצה קלה כבר מאחוריך")
    # Kept for the top bar until the notifications center replaces it.
    assert "alert" in body and "workouts" in body and "streak_weeks" in body
