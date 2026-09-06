"""
One row per user per discipline: the plan the AI coach actually prescribed.

A separate table rather than a column on `user` because people train for
several things at once - a 10K in December, a swim technique block, a
strength routine - and a single text field would mean each new plan
silently overwrote the last one.

Why store the plan at all: chat history is a rolling window of the most
recent turns (CHAT_HISTORY_LIMIT in app/core/claude_client.py), so a plan
given today has scrolled out of context by the time the user asks "how am I
doing against it?". Without a durable copy the coach reconstructs a
plausible-sounding plan and presents it as the original, which is worse than
admitting it forgot. The goal on `user` is not a substitute: it records where
the user wants to get to, not what was prescribed to get there.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class TrainingPlan(SQLModel, table=True):
    __tablename__ = "training_plan"
    # One current plan per discipline. Setting the same discipline again
    # replaces that plan and leaves the others untouched, which is what makes
    # "update my swim plan" safe when a running plan also exists.
    __table_args__ = (UniqueConstraint("user_id", "discipline", name="uq_training_plan_user_discipline"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    # Constrained to a fixed vocabulary by the tool schema Claude sees, not
    # by a DB enum: free text would drift ("ריצה" one week, "running" the
    # next) and quietly defeat the uniqueness constraint above, while a DB
    # enum would need a migration every time a new sport is worth supporting.
    discipline: str = Field(index=True)

    plan: str

    # When the plan was written. The coach needs this to tell a plan from
    # last week apart from one from two months ago - the older it is, the
    # more it should be revised against what the user has actually done
    # since, rather than recited as though still current.
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
