"""
Pydantic schemas for the Garmin-linking endpoints. GarminLinkRequest is
never persisted as-is - its password field only ever passes through
garmin_client.login_to_garmin() and is discarded immediately after.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class GarminLinkRequest(BaseModel):
    email: str
    password: str


class GarminStatus(BaseModel):
    linked: bool
    linked_at: Optional[datetime] = None
    last_sync_at: Optional[datetime] = None
    last_sync_error: Optional[str] = None
