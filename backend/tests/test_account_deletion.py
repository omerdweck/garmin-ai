"""
"Delete my account" must leave nothing behind, and must not touch anyone
else. There is no ON DELETE CASCADE in this schema, so a table the deletion
forgets either keeps the user's data or makes Postgres refuse the delete.
"""

from datetime import date

from sqlalchemy import func, select

from app.models.achievement import Achievement
from app.models.athlete_profile import AthleteProfile, CoachNote
from app.models.calorie_entry import CalorieBurnOverride, CalorieEntry
from app.models.chat_message import ChatMessage
from app.models.coach_event import CoachEvent
from app.models.daily_metric import DailyMetric
from app.models.join_request import STATUS_APPROVED, JoinRequest
from app.models.meal_plan import MealOption, MealPlan
from app.models.personal_record import PersonalRecord
from app.models.plan_session import PlanSession
from app.models.training_plan import TrainingPlan
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.services.account_lifecycle import _user_owned_tables, delete_user_completely
from app.services.signup_control import is_approved


def _fill_every_table(session, make_activity, user):
    """One row in every user-owned table, including the child tables."""
    make_activity(user)
    session.add(DailyMetric(user_id=user.id, date=date.today()))
    session.add(ChatMessage(user_id=user.id, role="user", content="hi"))
    session.add(CoachEvent(user_id=user.id, kind="post_workout", key="post_workout:1"))
    session.add(AthleteProfile(user_id=user.id, limitations="ברך"))
    session.add(CoachNote(user_id=user.id, text="מעדיף בוקר"))
    session.add(Achievement(user_id=user.id, key="first:running"))
    session.add(UsageEvent(user_id=user.id, kind="chat", model="m"))
    session.add(CalorieEntry(user_id=user.id, entry_date=date.today(), calories=500))
    session.add(CalorieBurnOverride(user_id=user.id, date=date.today(), calories=2400))
    session.add(PersonalRecord(user_id=user.id, record_type="run_5k", value=1500))

    plan = TrainingPlan(user_id=user.id, discipline="running", plan="p")
    meal = MealPlan(user_id=user.id, approach="a", target_calories=2000)
    session.add_all([plan, meal])
    session.commit()
    session.add(PlanSession(user_id=user.id, plan_id=plan.id, day_of_week=0, title="t"))
    session.add(MealOption(user_id=user.id, plan_id=meal.id, meal_slot="breakfast", title="t"))
    session.commit()


def _rows_for(session, user_id):
    return {
        table.name: session.execute(
            select(func.count()).select_from(table).where(table.c.user_id == user_id)
        ).scalar_one()
        for table in _user_owned_tables()
    }


def test_the_fixture_really_fills_every_table(session, make_user, make_activity):
    # Guards the test below: if a new table appears and isn't filled here,
    # "zero rows after deletion" would pass without proving anything.
    me = make_user()
    _fill_every_table(session, make_activity, me)

    empty = [name for name, count in _rows_for(session, me.id).items() if count == 0]
    assert empty == [], f"add a row for these tables to _fill_every_table: {empty}"


def test_deletion_removes_every_row_of_the_user(session, make_user, make_activity):
    me = make_user()
    _fill_every_table(session, make_activity, me)

    delete_user_completely(session, me.id)

    assert session.get(User, me.id) is None
    leftovers = {name: n for name, n in _rows_for(session, me.id).items() if n}
    assert leftovers == {}


def test_deletion_does_not_touch_other_users(session, make_user, make_activity):
    me, other = make_user(), make_user()
    _fill_every_table(session, make_activity, me)
    _fill_every_table(session, make_activity, other)
    before = _rows_for(session, other.id)

    delete_user_completely(session, me.id)

    assert session.get(User, other.id) is not None
    assert _rows_for(session, other.id) == before


def _approved_request(session, chat_id):
    session.add(JoinRequest(
        telegram_chat_id=chat_id,
        display_name="Omer",
        telegram_username="omer",
        status=STATUS_APPROVED,
    ))
    session.commit()


def test_deletion_also_removes_the_join_request(session, make_user):
    # The join request is keyed by chat id, not user id, so the generic
    # user_id sweep cannot see it - it has to be deleted by name.
    me = make_user()
    chat_id = me.telegram_chat_id
    _approved_request(session, chat_id)

    delete_user_completely(session, me.id)

    remaining = session.exec(select(JoinRequest).where(JoinRequest.telegram_chat_id == chat_id)).all()
    assert remaining == []


def test_returning_after_deletion_needs_approval_again(session, make_user):
    # The behaviour that matters, not the row: a person who deleted their
    # account is a stranger again and must be let in by the owner.
    me = make_user()
    chat_id = me.telegram_chat_id
    _approved_request(session, chat_id)
    assert is_approved(session, chat_id)

    delete_user_completely(session, me.id)

    assert not is_approved(session, chat_id)


def test_deletion_keeps_other_peoples_join_requests(session, make_user):
    me, other = make_user(), make_user()
    _approved_request(session, me.telegram_chat_id)
    _approved_request(session, other.telegram_chat_id)

    delete_user_completely(session, me.id)

    assert is_approved(session, other.telegram_chat_id)


def test_deletion_returns_the_chat_id_for_clearing_memory(session, make_user):
    me = make_user()
    chat_id = me.telegram_chat_id

    assert delete_user_completely(session, me.id) == chat_id
