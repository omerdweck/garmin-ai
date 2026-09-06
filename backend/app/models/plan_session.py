"""
One prescribed workout inside a training plan.

The plan itself stays free text - "where we are going and why" is prose, and
forcing it into columns would lose the reasoning. What a *week* looks like is
not prose, though: answering "what do I have tomorrow" from a paragraph
saying "three runs a week" is impossible, and that question has to be
answered every single evening in the daily summary.

So the plan keeps its narrative and gains a structured week beside it. The
week repeats until the coach revises it, which is also how a real coach
works - a rigid twelve-week schedule written on day one is wrong by week
three, once the person's actual training says something different.

Rest days are stored explicitly rather than inferred from an absent row. A
missing day is ambiguous - it could mean rest, or it could mean the coach
never filled it in - and "tomorrow is a rest day" needs to be something we
know, not something we assume from silence.
"""

from typing import Optional

from sqlmodel import Field, SQLModel

# 0 = Sunday, matching the Israeli week the bot's users actually train on,
# and matching Garmin's own firstDayOfWeek for this account.
DAY_NAMES_HE = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]

# What Claude sends. Mapped to the index above rather than being asked for a
# number, because "sunday" cannot be off by one and an integer can.
DAY_KEYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]


class PlanSession(SQLModel, table=True):
    __tablename__ = "plan_session"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Denormalised from the parent plan so this table is scoped like every
    # other one - it is what lets account deletion pick it up automatically
    # (see _user_owned_tables) and keeps per-user queries a single lookup.
    user_id: int = Field(foreign_key="user.id", index=True)
    plan_id: int = Field(foreign_key="training_plan.id", index=True)

    day_of_week: int = Field(index=True)

    # Deliberately no uniqueness on (plan, day): two-a-days are real, and a
    # constraint forbidding them would be a modelling opinion with no basis.
    title: str
    details: Optional[str] = Field(default=None)

    is_rest: bool = Field(default=False)

    # All optional: a strength session has no pace, an easy run may have no
    # target distance. Pace stays free text ("5:30/ק\"מ", "קצב שיחה") because
    # a coach's instruction is often a range or a feeling, not a number.
    target_distance_km: Optional[float] = Field(default=None)
    target_duration_minutes: Optional[int] = Field(default=None)
    target_pace: Optional[str] = Field(default=None)
