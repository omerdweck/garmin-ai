"""
Conversation memory for the AI coach. The Claude API is stateless - it
has no memory of previous calls - so holding a real back-and-forth
("earlier you said your goal was...") means *we* store the history and
replay the recent part of it on every request. That's what this table is.

Only the plain user/assistant text is kept, not the intermediate
tool_use/tool_result blocks from a turn: those are large, only meaningful
within the turn that produced them, and replaying them would grow the
context (and cost) for no benefit, since the model can always call the
tool again for fresh data.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    # "user" or "assistant" - matches the Claude API's own role values so
    # rows can be handed to the API without translation.
    role: str
    content: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
