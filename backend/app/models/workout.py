"""
A structured workout the coach built - stored in our own schema
(services/workout_schema.py), and, once sent, linked to the copy in the
athlete's Garmin library.

`sent_json` is exactly what was posted to Garmin, kept on purpose: this is
the one place the app writes to someone's Garmin account, through an
unofficial API, and "what did we send?" must always have an answer.
"""

from datetime import date as date_type
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

STATUS_DRAFT = "draft"      # built, no day chosen yet
STATUS_PLANNED = "planned"  # saved in the bot for a day, not on the watch
STATUS_SENT = "sent"        # on the Garmin calendar
STATUS_REMOVED = "removed"


class StoredWorkout(SQLModel, table=True):
    __tablename__ = "workout"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    sport: str
    name: str
    spec: dict = Field(sa_column=Column(JSONB, nullable=False))
    source: str = "coach"
    status: str = Field(default=STATUS_DRAFT, index=True)
    # Set on workouts the coach built together as one week (build_week).
    # A revised week replaces the previous one by this id, and the review
    # buttons walk through exactly the workouts that belong to it.
    proposal_id: Optional[str] = Field(default=None, index=True)
    # A new version of an existing workout ("✏️ שינוי עם המאמן"). The old one
    # stays until this one is saved or sent - then it is removed, from the
    # watch too, so editing never leaves the athlete with neither or both.
    replaces_workout_id: Optional[int] = None

    scheduled_for: Optional[date_type] = None
    garmin_workout_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    garmin_schedule_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    sent_json: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    sent_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
