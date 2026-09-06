"""
A user's linked Garmin Connect account. Kept as its own table (not
fields on User) since linking/unlinking Garmin is a separate concern
from site identity - a user can exist with or without one.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class GarminAccount(SQLModel, table=True):
    __tablename__ = "garmin_account"

    id: Optional[int] = Field(default=None, primary_key=True)

    # one Garmin account per site user, for now
    user_id: int = Field(foreign_key="user.id", unique=True, index=True)

    # Fernet-encrypted JSON session-token bundle from garmin_client.login_to_garmin().
    # Never the Garmin password - that's never stored anywhere.
    encrypted_token: str

    linked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Set when the user disconnects themselves. The row and its token are
    # deliberately kept, so reconnecting is one tap with no password - a
    # user-initiated pause is not the same event as a token Garmin has
    # rejected, which really is unusable and gets the row deleted instead.
    # NULL means active.
    disconnected_at: Optional[datetime] = Field(default=None)

    # updated by the (future) scheduled sync job - lets the admin dashboard
    # surface "which users' Garmin sync is failing" per the architecture doc
    last_sync_at: Optional[datetime] = Field(default=None)
    last_sync_error: Optional[str] = Field(default=None)

    # When the user's watch last uploaded to Garmin Connect - distinct from
    # last_sync_at above, which is when *we* last pulled from Garmin. The two
    # answer different questions, and confusing them is exactly what makes a
    # stale-data complaint hard to diagnose: our sync can succeed perfectly
    # and still return nothing new, because Garmin only holds what the watch
    # has uploaded. Stored so the value can be shown without re-querying
    # Garmin on every message.
    watch_last_upload_at: Optional[datetime] = Field(default=None)

    # When we last told this user their watch looks stale. Compared against
    # watch_last_upload_at rather than against "now": the scheduled sync runs
    # twice a day, so warning on every stale run would mean fourteen
    # identical nags for one forgotten week. Once the watch uploads again,
    # watch_last_upload_at moves past this timestamp and a later staleness
    # is treated as a new episode worth one new warning.
    watch_stale_notified_at: Optional[datetime] = Field(default=None)
