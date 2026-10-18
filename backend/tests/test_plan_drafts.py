"""
A plan the coach writes is a proposal. It must not replace the plan the
user is following until they press approve - that is the whole contract
behind the approve button.
"""

from sqlmodel import select

from app.core.claude_client import _execute_tool
from app.models.plan_session import PlanSession
from app.models.training_plan import TrainingPlan


def _plans(session, user):
    return session.exec(select(TrainingPlan).where(TrainingPlan.user_id == user.id)).all()


def test_coach_plan_is_saved_as_inactive_draft(session, make_user):
    me = make_user()
    _execute_tool(session, me, "set_training_plan", {"discipline": "running", "plan": "draft"})

    [plan] = _plans(session, me)
    assert plan.is_active is False


def test_draft_does_not_replace_the_active_plan(session, make_user):
    me = make_user()
    session.add(TrainingPlan(user_id=me.id, discipline="running", plan="current", is_active=True))
    session.commit()

    _execute_tool(session, me, "set_training_plan", {"discipline": "running", "plan": "proposal"})

    by_status = {p.is_active: p.plan for p in _plans(session, me)}
    assert by_status == {True: "current", False: "proposal"}


def test_revising_a_draft_updates_it_instead_of_adding_another(session, make_user):
    me = make_user()
    for text in ("v1", "v2", "v3"):
        _execute_tool(session, me, "set_training_plan", {"discipline": "running", "plan": text})

    [plan] = _plans(session, me)
    assert plan.plan == "v3"


def test_new_week_replaces_old_sessions_rather_than_merging(session, make_user):
    me = make_user()
    _execute_tool(session, me, "set_training_plan", {
        "discipline": "running",
        "plan": "v1",
        "sessions": [
            {"day": "sunday", "title": "אינטרוולים"},
            {"day": "tuesday", "title": "ריצה קלה"},
        ],
    })
    _execute_tool(session, me, "set_training_plan", {
        "discipline": "running",
        "plan": "v2",
        "sessions": [{"day": "monday", "title": "ריצה ארוכה"}],
    })

    titles = [s.title for s in session.exec(select(PlanSession).where(PlanSession.user_id == me.id)).all()]
    assert titles == ["ריצה ארוכה"]


def test_unknown_day_from_the_model_is_ignored(session, make_user):
    me = make_user()
    _execute_tool(session, me, "set_training_plan", {
        "discipline": "running",
        "plan": "p",
        "sessions": [{"day": "funday", "title": "x"}, {"day": "friday", "title": "y"}],
    })

    days = [s.day_of_week for s in session.exec(select(PlanSession).where(PlanSession.user_id == me.id)).all()]
    assert days == [5]
