"""
What the user says they ate, and - when the watch was not worn - what they
say they burned.

Two tables rather than one with a `kind` column, because they have different
cardinality and different semantics. Intake is many rows a day that add up:
800 at breakfast plus 400 at lunch is 1,200, and keeping them as separate
rows is what makes a single mistaken entry removable and leaves an audit of
when each was logged. A burn override is one figure for a whole day that
*replaces* a missing Garmin number - there is nothing to add up.
"""

from datetime import date as date_type
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class CalorieEntry(SQLModel, table=True):
    __tablename__ = "calorie_entry"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    # The local calendar day the food belongs to, not the moment it was
    # logged. Someone entering last night's dinner at 1am means yesterday,
    # and created_at below keeps the logging time separately.
    entry_date: date_type = Field(index=True)

    calories: int

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CalorieBurnOverride(SQLModel, table=True):
    __tablename__ = "calorie_burn_override"

    # One per day: a second value for the same date replaces the first rather
    # than adding to it, which is the whole difference from CalorieEntry.
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_calorie_burn_override_user_date"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    date: date_type = Field(index=True)

    calories: int

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
