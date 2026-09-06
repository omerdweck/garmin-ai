"""
Someone asking to be let in, and the owner's answer.

Keyed by telegram_chat_id rather than user_id because it exists *before* a
User does - no User row is created until a Garmin account links successfully,
and that only happens after approval. Keeping pending people out of the user
table also keeps the user count, quotas and deletion logic meaning exactly
what they say.

Replaces a shared invite code. A code passed around a group chat stops being
a secret the moment it is forwarded, and the owner never learns it happened;
an approval names the person asking, before they are in.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Column
from sqlmodel import Field, SQLModel

# Deliberately plain strings rather than an enum column: the set is tiny and
# a DB enum would need a migration to add a state.
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


class JoinRequest(SQLModel, table=True):
    __tablename__ = "join_request"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Unique: one standing request per chat. A second /start updates the row
    # rather than queueing a duplicate the owner would have to answer twice.
    # BIGINT for the same reason as User.telegram_chat_id: Telegram ids have
    # outgrown 32 bits.
    telegram_chat_id: int = Field(
        sa_column=Column(BigInteger, unique=True, index=True, nullable=False),
    )

    # Both are supplied by the requester and can be anything they like - a
    # display name is not identity. Stored so the owner has something to
    # recognise, and shown alongside the numeric chat id precisely because
    # that is the part nobody can choose.
    display_name: Optional[str] = Field(default=None)
    telegram_username: Optional[str] = Field(default=None)

    status: str = Field(default=STATUS_PENDING, index=True)

    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: Optional[datetime] = Field(default=None)
