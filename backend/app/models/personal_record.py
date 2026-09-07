"""
Garmin's personal records, mirrored so a new one can be noticed.

Read from Garmin rather than computed from our own activity rows, because
most of these are *best segments within* a workout - the fastest continuous
5 km inside a 10 km run - and we store only per-activity summaries. Garmin
has the per-second data and we do not.

The reason to store them at all is change detection: Garmin reports the
current record, never "this one is new". Keeping the previous value is what
turns the next sync into "you just beat it".
"""

from datetime import date as date_type
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


class PersonalRecord(SQLModel, table=True):
    __tablename__ = "personal_record"
    # One row per record type per user - a new best replaces the old value
    # in place, which is what makes the previous value available to compare
    # against exactly once.
    __table_args__ = (
        UniqueConstraint("user_id", "record_type", name="uq_personal_record_user_type"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    # Our own key, not Garmin's numeric typeId - see RECORD_TYPES in
    # app/services/records.py for why only some of theirs are mapped.
    record_type: str = Field(index=True)

    # Seconds for a time record, metres for a distance one. Which it is comes
    # from the record type, not from the value.
    value: float

    achieved_at: Optional[date_type] = Field(default=None)
    # BigInteger, matching Activity: Garmin's activity ids are already past
    # 20,000,000,000 and would overflow a 32-bit INTEGER column.
    garmin_activity_id: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )

    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
