"""
A reward the athlete earned - a distance milestone, a streak of weeks, a
first workout in a new sport. Shown inside the post-workout message.

`key` names the achievement itself ("distance:running:500"), unique per
user, so each one is granted exactly once however many times the sync that
found it runs again.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Achievement(SQLModel, table=True):
    __tablename__ = "achievement"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_achievement_user_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    key: str
    # The workout that earned it, so the message can say "this run".
    # Not a foreign key on purpose: a manual workout can be deleted by its
    # owner, and the achievement it earned should not block that or vanish.
    activity_id: Optional[int] = None
    details: Optional[dict] = Field(default=None, sa_column=Column(JSONB))
    achieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
