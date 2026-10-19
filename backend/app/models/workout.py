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

STATUS_DRAFT = "draft"
STATUS_SENT = "sent"
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

    scheduled_for: Optional[date_type] = None
    garmin_workout_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    garmin_schedule_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    sent_json: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    sent_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
