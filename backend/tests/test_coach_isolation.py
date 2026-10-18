"""
The most important property of the AI coach: a tool call made on behalf of
one user can never read or change another user's data.

Every tool receives the user from the caller, never from the model. These
tests pin that down by giving a second user data that would be easy to leak,
then asking every tool for the first user's view.
"""

import json
from datetime import date

import pytest
from sqlmodel import select

from app.core.claude_client import TOOLS, _execute_tool
from app.models.athlete_profile import AthleteProfile, CoachNote
from app.models.daily_metric import DailyMetric
from app.models.meal_plan import MealPlan
from app.models.metric_alert import MetricAlert, MetricBaseline
from app.models.personal_record import PersonalRecord
from app.models.training_plan import TrainingPlan

SECRET = "OTHER-USERS-SECRET"

READ_TOOLS = {
    "get_recent_daily_metrics": {"days": 30},
    "get_recent_activities": {"limit": 30},
    "get_training_goal": {},
    "get_training_plans": {},
    "get_personal_records": {},
    "get_calorie_status": {"days": 30},
    "get_meal_plan": {},
    "get_athlete_profile": {},
    "get_health_alerts": {},
}


@pytest.fixture
def two_users(session, make_user, make_activity):
    me = make_user()
    other = make_user(training_goal=SECRET, daily_calorie_target=2000)

    make_activity(other, activity_name=SECRET)
    session.add(DailyMetric(user_id=other.id, date=date.today(), hrv_status=SECRET))
    session.add(TrainingPlan(user_id=other.id, discipline="running", plan=SECRET))
    session.add(PersonalRecord(user_id=other.id, record_type="run_5k", value=1234.5))
    session.add(MealPlan(user_id=other.id, approach=SECRET, target_calories=2000))
    session.add(CoachNote(user_id=other.id, text=SECRET))
    session.add(AthleteProfile(user_id=other.id, limitations=SECRET))
    session.add(MetricAlert(user_id=other.id, rule=SECRET, started_on=date.today(), last_seen_on=date.today()))
    session.add(MetricBaseline(
        user_id=other.id, metric=SECRET, mean=1234.5, std=1, samples=28,
        window_start=date.today(), window_end=date.today(),
    ))
    session.commit()
    return me, other


def test_every_read_tool_is_covered():
    # A new read tool added to TOOLS without being added here would escape
    # the isolation check entirely - this makes that a failing test instead.
    read_tools = {t["name"] for t in TOOLS if t["name"].startswith("get_")}
    assert read_tools == set(READ_TOOLS)


@pytest.mark.parametrize("tool_name", sorted(READ_TOOLS))
def test_read_tools_never_return_another_users_data(session, two_users, tool_name):
    me, _ = two_users
    result = _execute_tool(session, me, tool_name, READ_TOOLS[tool_name])

    assert SECRET not in result
    # The record value is a number, not text, so it needs its own check.
    assert "1234.5" not in result


def test_saving_a_plan_does_not_touch_another_users_plan(session, two_users):
    me, other = two_users
    _execute_tool(session, me, "set_training_plan", {"discipline": "running", "plan": "my plan"})

    others_plan = session.exec(select(TrainingPlan).where(TrainingPlan.user_id == other.id)).one()
    assert others_plan.plan == SECRET
    assert others_plan.is_active is True


def test_deleting_a_plan_does_not_touch_another_users_plan(session, two_users):
    me, other = two_users
    result = json.loads(
        _execute_tool(session, me, "delete_training_plan", {"discipline": "running"})
    )

    assert result == {"deleted": False}  # I had no running plan to delete
    assert len(session.exec(select(TrainingPlan).where(TrainingPlan.user_id == other.id)).all()) == 1


def test_deleting_a_meal_plan_does_not_touch_another_users_plan(session, two_users):
    me, other = two_users
    _execute_tool(session, me, "delete_meal_plan", {})

    assert len(session.exec(select(MealPlan).where(MealPlan.user_id == other.id)).all()) == 1


@pytest.mark.parametrize("tool, args", [
    ("update_athlete_profile", {"limitations": "mine"}),
    ("remember", {"note": "mine"}),
])
def test_profile_writes_stay_with_their_owner(session, two_users, tool, args):
    me, other = two_users
    _execute_tool(session, me, tool, args)

    assert session.exec(select(CoachNote).where(CoachNote.user_id == other.id)).one().text == SECRET
    assert session.exec(select(AthleteProfile).where(AthleteProfile.user_id == other.id)).one().limitations == SECRET
