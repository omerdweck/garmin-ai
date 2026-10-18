"""
Work that waits on Claude runs on its own queue and worker, and scheduled
AI work stops when the monthly budget is spent.
"""

from unittest.mock import patch

import pytest

import app.tasks  # noqa: F401  - registers the tasks
from app.celery_app import COACH_QUEUE, celery_app
from app.models.usage_event import UsageEvent
from app.tasks import send_daily_summary_task, send_weekly_summary_task

AI_TASKS = {
    "app.tasks.send_daily_summary_task",
    "app.tasks.send_weekly_summary_task",
    "app.tasks.deliver_coach_event_task",
}


def _queue(task_name):
    route = celery_app.amqp.router.route({}, task_name)
    return route["queue"].name


@pytest.mark.parametrize("task_name", sorted(AI_TASKS))
def test_ai_tasks_go_to_the_coach_queue(task_name):
    assert _queue(task_name) == COACH_QUEUE


def test_everything_else_stays_on_the_default_queue():
    # A sync must never wait behind a summary.
    others = [n for n in celery_app.tasks if n.startswith("app.tasks.") and n not in AI_TASKS]
    assert others, "expected sync and dispatch tasks"
    assert {_queue(n) for n in others} == {"celery"}


@pytest.fixture
def over_budget(session, make_user):
    me = make_user()
    session.add(UsageEvent(user_id=me.id, kind="chat", model="m", cost_usd=10_000.0))
    session.commit()
    return me


def test_daily_summary_stops_when_the_budget_is_spent(over_budget):
    with patch("app.tasks.generate_daily_summary") as generate:
        send_daily_summary_task(over_budget.id)
    generate.assert_not_called()


def test_weekly_summary_stops_when_the_budget_is_spent(over_budget):
    with patch("app.tasks.generate_weekly_summary") as generate:
        send_weekly_summary_task(over_budget.id)
    generate.assert_not_called()


def test_summary_runs_while_under_budget(session, make_user):
    me = make_user()
    with patch("app.tasks.generate_daily_summary", return_value=None) as generate:
        send_daily_summary_task(me.id)
    generate.assert_called_once()
