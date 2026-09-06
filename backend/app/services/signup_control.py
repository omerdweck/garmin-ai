"""
Who is allowed to become a user at all.

Two gates, in order: a hard user cap, then the owner's explicit approval of
this specific person. Neither touches anyone already registered - revoking
an existing user is /admin limit <id> 0, and conflating the two would mean
lowering the cap silently locked people out.

Approval replaced a shared invite code. A code forwarded in a group chat
stops being a secret and the owner never finds out; an approval names the
person before they are in. The cost is that someone may wait for an answer,
which for a bot serving about ten friends is not a real cost.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, func, select

from app.core.config import settings
from app.models.join_request import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    JoinRequest,
)
from app.models.user import User

logger = logging.getLogger(__name__)


def user_count(session: Session) -> int:
    return session.exec(select(func.count()).select_from(User)).one()


def is_full(session: Session) -> bool:
    return user_count(session) >= settings.max_users


def get_request(session: Session, chat_id: int) -> Optional[JoinRequest]:
    return session.exec(
        select(JoinRequest).where(JoinRequest.telegram_chat_id == chat_id)
    ).first()


def create_or_refresh_request(
    session: Session,
    chat_id: int,
    display_name: Optional[str],
    username: Optional[str],
) -> JoinRequest:
    """
    Record a request to join, or refresh the details on an existing one.

    A rejected request is deliberately NOT reopened here: someone told no
    should not be able to re-ask their way in by pressing the button again.
    The owner can still change their mind with /admin approve.
    """
    existing = get_request(session, chat_id)
    if existing is not None:
        existing.display_name = display_name
        existing.telegram_username = username
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    request = JoinRequest(
        telegram_chat_id=chat_id,
        display_name=display_name,
        telegram_username=username,
    )
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


def decide(session: Session, chat_id: int, approved: bool) -> Optional[JoinRequest]:
    request = get_request(session, chat_id)
    if request is None:
        return None

    request.status = STATUS_APPROVED if approved else STATUS_REJECTED
    request.decided_at = datetime.now(timezone.utc)
    session.add(request)
    session.commit()
    session.refresh(request)
    logger.info("Join request for chat %s -> %s", chat_id, request.status)
    return request


def is_approved(session: Session, chat_id: int) -> bool:
    """
    The owner's own chat is always allowed. Without this, a fresh deployment
    has nobody able to approve the first person - including the person who
    would be doing the approving.
    """
    if settings.is_admin_configured and chat_id == settings.admin_chat_id:
        return True

    request = get_request(session, chat_id)
    return request is not None and request.status == STATUS_APPROVED


def pending_requests(session: Session) -> list[JoinRequest]:
    return list(
        session.exec(
            select(JoinRequest)
            .where(JoinRequest.status == STATUS_PENDING)
            .order_by(JoinRequest.requested_at)
        ).all()
    )


def signup_status(session: Session) -> dict:
    """Everything /admin needs to say about the front door in one call."""
    count = user_count(session)
    return {
        "users": count,
        "max_users": settings.max_users,
        "full": count >= settings.max_users,
        "pending": len(pending_requests(session)),
        "approver_configured": settings.is_admin_configured,
    }


def rejection_reason(session: Session) -> Optional[str]:
    """
    User-facing Hebrew for why a newcomer cannot even ask, or None if they
    may request access.
    """
    if is_full(session):
        logger.warning("Signup refused - at capacity (%s/%s)", user_count(session), settings.max_users)
        return (
            "🚧 הבוט מלא כרגע.\n\n"
            "מספר המשתמשים מוגבל. אם הוזמנת, פנה למי ששלח לך את הקישור."
        )

    if not settings.is_admin_configured:
        # Fail loudly rather than leaving someone tapping a button whose
        # request nobody will ever see.
        logger.error("Signup impossible - ADMIN_CHAT_ID is not configured, nobody can approve")
        return "🚧 ההרשמה סגורה כרגע. נסה שוב מאוחר יותר."

    return None
