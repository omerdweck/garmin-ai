"""
One row per Claude API call: who triggered it, what it cost, and why.

Recorded so "which friend is burning the credits" has an answer that comes
from measurement rather than guesswork. A single conversational turn can
make several calls (the tool-use loop runs one per round), and each is
recorded separately - the round count is itself a useful signal when a
user's costs look wrong.

The cost is stored, not derived at query time, because prices change: an
event from last month should keep the price that actually applied to it.
The token counts are the durable source of truth; cost_usd is a
convenience for reporting and is NULL when the model's price is unknown,
rather than silently guessing a number someone might act on.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class UsageEvent(SQLModel, table=True):
    __tablename__ = "usage_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    # Indexed because every report is "this user, this month".
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # "chat" or "daily_summary" - separates cost the user chose to incur
    # from cost the schedule incurred on their behalf, which matters when
    # deciding whether a quota was actually the user's doing.
    kind: str = Field(index=True)

    # Stored per event rather than assumed from settings: switching tiers
    # must not retroactively reprice history.
    model: str

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    # Float rather than Numeric: individual values are fractions of a cent
    # and these are summed for reporting, never used to bill anyone. The
    # integer token counts above remain exact, so a cost can always be
    # recomputed from them if precision ever matters.
    cost_usd: Optional[float] = Field(default=None)
