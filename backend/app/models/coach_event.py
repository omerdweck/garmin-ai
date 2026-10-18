"""
One thing the coach may tell a user without being asked - a finished
workout, a metric drifting out of the user's normal range.

The row exists so each event is told exactly once. `key` names the event
itself (e.g. "post_workout:<activity id>"), and (user_id, key) is unique:
recording the same event twice - a sync that runs again, a task delivered
twice - finds the existing row and does nothing.

`status` says what happened to it, `skip_reason` why it was not sent, so
"why didn't I get a message?" has an answer in the database.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

KIND_POST_WORKOUT = "post_workout"
KIND_ANOMALY = "anomaly"

STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_SKIPPED = "skipped"


class CoachEvent(SQLModel, table=True):
    __tablename__ = "coach_event"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_coach_event_user_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    kind: str = Field(index=True)
    key: str
    # Whatever the composer needs to write the message (activity id, which
    # metric drifted and by how much). Facts, not the finished text.
    payload: Optional[dict] = Field(default=None, sa_column=Column(JSONB))

    status: str = Field(default=STATUS_PENDING, index=True)
    skip_reason: Optional[str] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    sent_at: Optional[datetime] = None
