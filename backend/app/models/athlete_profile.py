"""
What the coach knows about the athlete beyond their numbers - and keeps
knowing between conversations.

Chat history is a rolling window of the last few turns, so anything said a
week ago ("I only have Tuesdays and Saturdays", "my left knee hurts on
descents") used to be gone. These two tables are the long-term memory:

  AthleteProfile  structured facts the plans are built from - one row per
                  user, every field optional.
  CoachNote       short free-text facts the coach chose to remember.

Both are things the *user* said, never conclusions drawn about them: the
tool descriptions tell the coach so, and the user can read and delete all
of it from /profile.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

SPORTS = ["running", "cycling", "swimming", "strength", "walking", "hiking", "hiit", "yoga", "mobility", "other"]
EXPERIENCE_LEVELS = ["beginner", "intermediate", "advanced"]

# Enough for a real picture of someone; few enough that the whole list fits
# in every prompt without crowding out the data.
MAX_NOTES = 40
MAX_NOTE_LENGTH = 300


class AthleteProfile(SQLModel, table=True):
    __tablename__ = "athlete_profile"
    __table_args__ = (UniqueConstraint("user_id", name="uq_athlete_profile_user"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    sports: Optional[list] = Field(default=None, sa_column=Column(JSONB))
    experience: Optional[str] = None
    # Day keys ("sunday".."saturday") the user can train on.
    training_days: Optional[list] = Field(default=None, sa_column=Column(JSONB))
    max_session_minutes: Optional[int] = None
    equipment: Optional[str] = None
    pool_length_m: Optional[int] = None
    ftp_watts: Optional[int] = None
    # Injuries and limits as the user described them, in their words.
    limitations: Optional[str] = None

    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CoachNote(SQLModel, table=True):
    __tablename__ = "coach_note"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
