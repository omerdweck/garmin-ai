"""
The User model - the site's own user table (not to be confused with a
linked Garmin account, which will get its own table later, since one
user can theoretically link/unlink a Garmin account without that
touching their site identity).
`table=True` turns this from a plain Pydantic model into a real
Postgres table.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str

    # management flags: is_active will be used for email verification (next step), is_admin for the admin panel
    is_active: bool = Field(default=False)
    is_admin: bool = Field(default=False)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
