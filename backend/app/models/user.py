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

from sqlalchemy import BigInteger, Column
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: Optional[str] = Field(default=None, unique=True, index=True)
    hashed_password: Optional[str] = None
    # BIGINT, not the INTEGER that a plain `int` would map to. Telegram ids
    # have outgrown 32 bits: accounts created in recent years get values like
    # 6123456789, well past INTEGER's 2,147,483,647 ceiling. Left as-is, the
    # first friend with a newer Telegram account would have failed to
    # register with an overflow error that says nothing about the cause.
    telegram_chat_id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, unique=True, index=True, nullable=True),
    )

    # management flags: is_active is set to True once the email verification link is used, is_admin for the admin panel
    is_active: bool = Field(default=False)
    is_admin: bool = Field(default=False)

    # one-time email verification token - set on register, cleared once used (or never used = still None)
    verification_token: Optional[str] = Field(default=None, index=True)
    verification_token_expires_at: Optional[datetime] = Field(default=None)

    # free-text training goal, read/written by the AI coach itself via
    # tool calls (see app/core/claude_client.py) - deliberately not a
    # structured schema (target pace/distance/date...) until we know what
    # structure is actually needed.
    training_goal: Optional[str] = None

    # Day (0=Sunday) and hour for the weekly summary, or NULL for neither.
    # Two columns rather than one because the week and the hour are chosen
    # separately in the picker, and a combined value would have to be split
    # again everywhere it is read.
    weekly_summary_day: Optional[int] = Field(default=None)
    weekly_summary_hour: Optional[int] = Field(default=None)

    # --- Calorie tracking -------------------------------------------------
    # NULL means the whole feature is off, which is what makes it opt-in
    # without a separate boolean: no button prompts, no reminders, no calorie
    # section in the daily summary, and the coach's tools report "not
    # tracking". Everything else in the feature keys off this one field.
    daily_calorie_target: Optional[int] = Field(default=None)

    # Up to three whole hours (Asia/Jerusalem) at which to remind the user to
    # log what they ate. Three columns rather than a table: the cap is three,
    # the picker offers whole hours, and the hourly dispatcher already selects
    # on an hour column - a join would buy nothing.
    calorie_reminder_hour_1: Optional[int] = Field(default=None)
    calorie_reminder_hour_2: Optional[int] = Field(default=None)
    calorie_reminder_hour_3: Optional[int] = Field(default=None)

    # When the user was last asked whether to stop tracking after a silent
    # stretch. Compared against their last entry rather than against "now",
    # so the question is asked once per episode of neglect and resets by
    # itself the moment they log something - the same shape as
    # watch_stale_notified_at on GarminAccount.
    calorie_abandon_prompted_at: Optional[datetime] = Field(default=None)

    # Per-user override of settings.daily_message_limit. NULL means "use the
    # global default" and 0 means "no coach access at all" - two distinct
    # states that a plain integer with a 0 default could not express, and the
    # difference matters: revoking one person's access must not look like a
    # configuration that was never set.
    daily_message_limit: Optional[int] = Field(default=None)

    # When the user accepted the terms of use. Recorded rather than a plain
    # boolean so there's an auditable "consented at this point in time" -
    # if the terms change, comparing against the revision date shows who
    # still needs to re-accept.
    terms_accepted_at: Optional[datetime] = None

    # Hour of day (0-23, Asia/Jerusalem) at which to send this user's daily
    # summary. None = the user hasn't chosen one / opted out, and no summary
    # is sent. Stored as an hour rather than a full time because the picker
    # only offers whole hours and Beat only wakes up hourly anyway.
    daily_summary_hour: Optional[int] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
