"""
One row per individual workout (run, swim, strength session...), unlike
DailyMetric which is one row per day. garmin_activity_id is Garmin's own
ID for the activity - the unique constraint on it is what makes re-sync
idempotent (running the sync twice for the same period must not create
duplicate rows).
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Activity(SQLModel, table=True):
    __tablename__ = "activity"
    __table_args__ = (UniqueConstraint("garmin_activity_id", name="uq_activity_garmin_activity_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    # Garmin's activity IDs exceed Postgres's default 32-bit "integer" range
    # (e.g. 24109347140), so this needs BigInteger explicitly.
    garmin_activity_id: int = Field(sa_column=Column(BigInteger, nullable=False))

    activity_name: Optional[str] = None
    activity_type: Optional[str] = None  # e.g. "strength_training", "running" - Garmin's activityType.typeKey
    start_time: datetime = Field(index=True)
    duration_seconds: Optional[float] = None
    distance_meters: Optional[float] = None
    calories: Optional[float] = None
    avg_heart_rate: Optional[float] = None
    max_heart_rate: Optional[float] = None
    steps: Optional[int] = None

    raw_json: Optional[dict] = Field(default=None, sa_column=Column(JSONB))

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
