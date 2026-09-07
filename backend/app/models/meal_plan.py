"""
A personal eating plan: a calorie target, the reasoning behind it, and
several options for each meal.

Options per slot rather than a weekly calendar, which is the difference from
training plans. "Monday you run 5km" is a schedule someone follows; "Monday
you eat an omelette" is a schedule nobody follows, because what is in the
fridge on Monday decides. Two or three choices per slot give variety and
survive contact with a real week.

One plan per user, unlike training plans which are one per discipline -
there is only one way to eat.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

# Slot keys. Constrained by the enum in the Claude tool schema rather than by
# a database enum, the same reasoning as `discipline` on TrainingPlan: a DB
# enum needs a migration to add a value, and the model is the only writer.
MEAL_SLOTS = ["breakfast", "lunch", "dinner", "snack"]

MEAL_SLOT_LABELS = {
    "breakfast": "🌅 ארוחת בוקר",
    "lunch": "☀️ ארוחת צהריים",
    "dinner": "🌙 ארוחת ערב",
    "snack": "🍎 ארוחת ביניים",
}


class MealPlan(SQLModel, table=True):
    __tablename__ = "meal_plan"
    __table_args__ = (UniqueConstraint("user_id", name="uq_meal_plan_user"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    # What the plan is built around, in prose - the same role `plan` plays on
    # TrainingPlan. Columns would lose the reasoning.
    approach: str

    # The intake the options add up to. Stored on the plan rather than read
    # from User.daily_calorie_target every time, so a plan keeps meaning what
    # it meant when it was written even after the target moves.
    target_calories: int

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MealOption(SQLModel, table=True):
    __tablename__ = "meal_option"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Denormalised from the parent so this table is scoped like every other
    # one - it is what lets account deletion pick it up automatically (see
    # _user_owned_tables in app/services/account_lifecycle.py).
    user_id: int = Field(foreign_key="user.id", index=True)
    plan_id: int = Field(foreign_key="meal_plan.id", index=True)

    meal_slot: str = Field(index=True)

    title: str
    description: Optional[str] = Field(default=None)
    calories: Optional[int] = Field(default=None)
