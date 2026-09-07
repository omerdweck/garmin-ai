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


SOURCE_GARMIN = "garmin"
SOURCE_MANUAL = "manual"


class Activity(SQLModel, table=True):
    __tablename__ = "activity"
    # Scoped to (user_id, garmin_activity_id), not garmin_activity_id alone:
    # an activity belongs to a user, and two users can legitimately hold the
    # same Garmin activity id - e.g. one person linked through both the
    # website and the bot, or a shared family Garmin account. A global
    # constraint silently made the second user's sync a no-op.
    __table_args__ = (
        UniqueConstraint("user_id", "garmin_activity_id", name="uq_activity_user_garmin_activity_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    # Garmin's activity IDs exceed Postgres's default 32-bit "integer" range
    # (e.g. 24109347140), so this needs BigInteger explicitly.
    # Nullable so a manually entered workout can exist at all - it has no
    # Garmin id by definition. Postgres allows any number of NULLs in a
    # unique index (NULL is never equal to NULL), so the uniqueness above
    # keeps making Garmin re-syncs idempotent while placing no limit on
    # manual entries.
    garmin_activity_id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, nullable=True))

    # Where this row came from. Needed for three real reasons, not for
    # bookkeeping: manual rows are marked in the UI, only manual rows may be
    # deleted by the user (a Garmin one would return on the next sync), and
    # the coach must know a missing heart rate means "there was no watch"
    # rather than "the watch failed to record it".
    source: str = Field(default=SOURCE_GARMIN, index=True)

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

    # Per-set breakdown for strength workouts (exercise category, reps,
    # weight). Comes from a separate Garmin endpoint, so it's fetched
    # lazily the first time someone actually opens that workout rather
    # than for every activity during sync - and cached here afterwards,
    # since a finished workout's sets never change.
    exercise_sets: Optional[dict] = Field(default=None, sa_column=Column(JSONB))

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
