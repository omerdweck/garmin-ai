"""
Who is allowed to become a user at all.

Two independent gates, because they fail differently. An invite code stops
strangers who stumble across the bot - Telegram bots are searchable by
username, so "nobody knows it exists" was never a control. A hard user cap
is the backstop for when the code leaks, which it eventually will: it is a
string passed around a group chat, not a secret.

Neither gate touches existing users. Someone already registered keeps
working even if the code changes or the cap is lowered below the current
count - revoking access is what /admin limit is for, and conflating the two
would mean tightening the cap silently locked people out.
"""

import logging
import secrets
from typing import Optional

from sqlmodel import Session, func, select

from app.core.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

# Wrong codes a single chat may try before this conversation stops
# accepting any. A short alphanumeric code is guessable at machine speed;
# without a ceiling the gate is decoration.
MAX_INVITE_ATTEMPTS = 5


def user_count(session: Session) -> int:
    return session.exec(select(func.count()).select_from(User)).one()


def is_full(session: Session) -> bool:
    return user_count(session) >= settings.max_users


def invite_code_matches(supplied: str) -> bool:
    """
    Constant-time comparison.

    `==` on strings returns as soon as two characters differ, so the time it
    takes leaks how much of the prefix was right - enough, over many tries,
    to recover the code character by character. compare_digest takes the
    same time whatever the input.
    """
    expected = settings.invite_code
    if not expected:
        # No code configured means the gate is open by design; callers check
        # settings.is_invite_required before ever asking.
        return True
    return secrets.compare_digest(supplied.strip(), expected.strip())


def signup_status(session: Session) -> dict:
    """Everything /admin needs to say about the front door in one call."""
    count = user_count(session)
    return {
        "users": count,
        "max_users": settings.max_users,
        "full": count >= settings.max_users,
        "invite_required": settings.is_invite_required,
    }


def rejection_reason(session: Session) -> Optional[str]:
    """
    User-facing Hebrew for why a newcomer cannot register, or None if they
    can proceed to the invite step.
    """
    if is_full(session):
        logger.warning(
            "Signup refused - at capacity (%s/%s)", user_count(session), settings.max_users
        )
        return (
            "🚧 הבוט מלא כרגע.\n\n"
            "מספר המשתמשים מוגבל. אם הוזמנת, פנה למי ששלח לך את הקישור."
        )
    return None
