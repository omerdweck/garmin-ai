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

    # updated by the (future) scheduled sync job - lets the admin dashboard
    # surface "which users' Garmin sync is failing" per the architecture doc
    last_sync_at: Optional[datetime] = Field(default=None)
    last_sync_error: Optional[str] = Field(default=None)
