"""
Everything the bot told a user without being asked, kept for the Mini App's
notifications screen (the bell).

A copy, not a view over coach_event + chat_message: those hold only some of
the unprompted messages (the weekly report, a new record or a stale-watch
warning never pass through coach_event), and chat_message is the coach's
memory, which the Mini App's database role is not allowed to read at all.
Storing the exact text that was sent, at the moment it was sent, is the one
shape that covers every kind of message and needs no permission it should
not have.

`source_key` names the message itself ("coach_event:17",
"weekly_report:2026-10-24"), and (user_id, source_key) is unique - so a
retried task, or the backfill running a second time, records nothing new.

`read_at` is the only column the Mini App may change (see the migration).
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

KIND_POST_WORKOUT = "post_workout"
KIND_ANOMALY = "anomaly"
KIND_WEEKLY_REPORT = "weekly_report"
KIND_DAILY_SUMMARY = "daily_summary"
KIND_RECORD = "record"
KIND_REMINDER = "reminder"
KIND_WATCH = "watch"
KIND_ACCOUNT = "account"


class Notification(SQLModel, table=True):
    __tablename__ = "notification"
    __table_args__ = (
        UniqueConstraint("user_id", "source_key", name="uq_notification_user_source"),
        # The screen pages newest-first by id within one user; this is the
        # index that query and the unread count both walk.
        Index("ix_notification_user_id_id", "user_id", "id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")

    kind: str
    # Exactly what Telegram received, Markdown and all. The page turns the
    # *bold* and _italic_ marks into plain styled text itself.
    text: str
    # Where tapping it goes: {"type": "activity", "id": 123},
    # {"type": "tab", "tab": "health", "focus": "metric-avg_hrv"}, or None.
    link: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    source_key: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    read_at: Optional[datetime] = None
