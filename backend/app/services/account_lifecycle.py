"""
Account state transitions: what "connected", "disconnected" and "deleted"
actually mean, kept out of the bot handlers so the rules live in one
readable place rather than being spread across button callbacks.

The states a person can be in:

  UNKNOWN        no User row at all -> full onboarding
  NEEDS_TERMS    User exists but never accepted the terms -> blocked
  NEEDS_LINK     terms accepted, but no Garmin account (never linked, or
                 the token was rejected by Garmin and removed) -> asks for
                 the password again, but not for the terms
  DISCONNECTED   user paused it themselves; token still stored -> one-tap
                 reconnect, no password
  ACTIVE         normal use

Deliberate asymmetry between the two ways a link can end:
  - the user disconnecting is reversible, so the token is kept
  - Garmin rejecting the token is not, so the row goes (the user's data
    and terms acceptance still stay - only the credential is gone)
"""

from datetime import date as date_type
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Session, delete, select

from app.models.activity import Activity
from app.models.chat_message import ChatMessage
from app.models.daily_metric import DailyMetric
from app.models.garmin_account import GarminAccount
from app.models.user import User


class AccountState(str, Enum):
    UNKNOWN = "unknown"
    NEEDS_TERMS = "needs_terms"
    NEEDS_LINK = "needs_link"
    DISCONNECTED = "disconnected"
    ACTIVE = "active"


def get_account(session: Session, user_id: int) -> Optional[GarminAccount]:
    return session.exec(select(GarminAccount).where(GarminAccount.user_id == user_id)).first()


def find_user_by_chat(session: Session, chat_id: int) -> Optional[User]:
    return session.exec(select(User).where(User.telegram_chat_id == chat_id)).first()


def resolve_state(session: Session, chat_id: int) -> tuple[AccountState, Optional[User]]:
    """Single source of truth for 'what is this person allowed to do right now'."""
    user = find_user_by_chat(session, chat_id)
    if user is None:
        return AccountState.UNKNOWN, None

    if user.terms_accepted_at is None:
        return AccountState.NEEDS_TERMS, user

    account = get_account(session, user.id)
    if account is None:
        return AccountState.NEEDS_LINK, user
    if account.disconnected_at is not None:
        return AccountState.DISCONNECTED, user

    return AccountState.ACTIVE, user


def disconnect(session: Session, user_id: int) -> None:
    """User-initiated pause. Keeps the token and every row of their data."""
    account = get_account(session, user_id)
    if account is not None:
        account.disconnected_at = datetime.now(timezone.utc)
        session.add(account)
        session.commit()


def reconnect(session: Session, user_id: int) -> bool:
    """
    Reverses a disconnect. Returns True if the stored data is already
    current for today, so the caller can skip an unnecessary Garmin round
    trip - re-syncing data we already hold just spends time and adds load
    against Garmin's rate limits for no new information.
    """
    account = get_account(session, user_id)
    if account is None:
        return False

    account.disconnected_at = None
    session.add(account)
    session.commit()

    return account.last_sync_at is not None and account.last_sync_at.date() == date_type.today()


def delete_user_completely(session: Session, user_id: int) -> None:
    """
    Erases everything we hold about a user, after which they're
    indistinguishable from someone who never used the bot.

    Order matters: every other table carries a foreign key to user.id, so
    the children have to go before the parent or Postgres rejects the
    delete.
    """
    session.exec(delete(ChatMessage).where(ChatMessage.user_id == user_id))
    session.exec(delete(DailyMetric).where(DailyMetric.user_id == user_id))
    session.exec(delete(Activity).where(Activity.user_id == user_id))
    session.exec(delete(GarminAccount).where(GarminAccount.user_id == user_id))

    user = session.get(User, user_id)
    if user is not None:
        session.delete(user)

    session.commit()
