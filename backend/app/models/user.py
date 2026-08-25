"""
The User model - the site's own identity table (not to be confused with
a linked Garmin account, which lives in its own table, since one user
can theoretically link/unlink a Garmin account without that touching
their site identity).
`table=True` turns this from a plain Pydantic model into a real
Postgres table.

A User row now comes from one of two independent paths, both landing in
this same table so everything downstream (GarminAccount, DailyMetric,
Activity, chat history) only ever needs a plain user_id:
- website: email + hashed_password set, telegram_chat_id null
- Telegram bot: telegram_chat_id set, email/hashed_password null (the
  bot never asks for a site password - Telegram's own login is the
  only "auth" a bot-only user has)
Postgres unique indexes allow any number of NULLs (NULL is never equal
to NULL), so having many bot-only users with email=NULL, or many
website-only users with telegram_chat_id=NULL, is fine.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: Optional[str] = Field(default=None, unique=True, index=True)
    hashed_password: Optional[str] = None
    telegram_chat_id: Optional[int] = Field(default=None, unique=True, index=True)

    # management flags: is_active is set to True once the email verification link is used, is_admin for the admin panel
    is_active: bool = Field(default=False)
    is_admin: bool = Field(default=False)

    # one-time email verification token - set on register, cleared once used (or never used = still None)
    verification_token: Optional[str] = Field(default=None, index=True)
    verification_token_expires_at: Optional[datetime] = Field(default=None)

    # free-text training goal, read/written by the AI coach itself via
    # tool calls (see app/core/claude_client.py, added in a later stage) -
    # deliberately not a structured schema (target pace/distance/date...)
    # until we know what structure is actually needed.
    training_goal: Optional[str] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
