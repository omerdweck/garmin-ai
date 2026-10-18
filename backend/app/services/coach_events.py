"""
The pipeline every unprompted coach message goes through.

    record_event()  - something happened (a workout synced, a metric drifted).
                      Stores it once; recording the same event again is a no-op.
    deliver()       - decides whether it should be sent, writes the message,
                      sends it, and files it in the chat history.

Splitting the two is what keeps the rest of the code simple: the sync only
has to say "this workout is new", and every rule about *whether* the user
hears about it - their settings, the daily limit, the AI budget, whether
they are still connected - lives here, once, with tests.

What a message says is not decided here. Each kind of event registers a
composer (later phases add them: post-workout, anomaly), and this module
only routes to it.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.core import clock
from app.core.telegram_client import send_telegram_message
from app.models.chat_message import ChatMessage
from app.models.coach_event import (
    KIND_ANOMALY,
    KIND_POST_WORKOUT,
    STATUS_PENDING,
    STATUS_SENT,
    STATUS_SKIPPED,
    CoachEvent,
)
from app.models.user import User
from app.services.account_lifecycle import AccountState, resolve_state
from app.services.ai_budget import budget_exhausted

logger = logging.getLogger(__name__)

# The button under every unprompted message. It enters chat mode, so the
# natural reply - "why?", "what should I change?" - goes straight to a coach
# that already has this message in its history.
TALK_CALLBACK = "talk:start"
TALK_KEYBOARD = InlineKeyboardMarkup([[InlineKeyboardButton("💬 לדבר על זה", callback_data=TALK_CALLBACK)]])

# Which User column switches each kind off.
PREFERENCE_FOR_KIND = {
    KIND_POST_WORKOUT: "notify_post_workout",
    KIND_ANOMALY: "notify_anomalies",
}

# Kinds limited to one message per Israeli calendar day. A drifting metric
# is worth one message; three alerts in an afternoon is how a bot gets muted.
ONE_PER_DAY = {KIND_ANOMALY}


@dataclass(frozen=True)
class Composer:
    write: Callable[[Session, User, CoachEvent], Optional[str]]
    # True if writing the message calls Claude - those are the ones the
    # monthly AI budget stops. Template-only messages cost nothing.
    uses_ai: bool


COMPOSERS: dict[str, Composer] = {}


def register_composer(kind: str, *, uses_ai: bool):
    def decorator(fn):
        COMPOSERS[kind] = Composer(write=fn, uses_ai=uses_ai)
        return fn

    return decorator


def record_event(
    session: Session, user_id: int, kind: str, key: str, payload: Optional[dict] = None
) -> Optional[CoachEvent]:
    """
    Store an event once. Returns the new row, or None if this exact event
    was already recorded - so the caller can enqueue delivery only for
    genuinely new events.

    ON CONFLICT DO NOTHING rather than check-then-insert: two workers seeing
    the same workout at the same moment would both pass a check, and the
    database constraint is the only place that race is settled.
    """
    statement = (
        insert(CoachEvent)
        .values(
            user_id=user_id,
            kind=kind,
            key=key,
            payload=payload,
            status=STATUS_PENDING,
            created_at=clock.now_utc(),
        )
        .on_conflict_do_nothing(constraint="uq_coach_event_user_key")
        .returning(CoachEvent.id)
    )
    new_id = session.execute(statement).scalar_one_or_none()
    session.commit()
    return session.get(CoachEvent, new_id) if new_id is not None else None


def _skip(session: Session, event: CoachEvent, reason: str) -> str:
    event.status = STATUS_SKIPPED
    event.skip_reason = reason
    session.add(event)
    session.commit()
    logger.info("Coach event %s skipped: %s", event.id, reason)
    return f"skipped:{reason}"


def _already_sent_today(session: Session, user_id: int, kind: str, exclude_id: int) -> bool:
    sent = session.exec(
        select(CoachEvent.sent_at).where(
            CoachEvent.user_id == user_id,
            CoachEvent.kind == kind,
            CoachEvent.status == STATUS_SENT,
            CoachEvent.id != exclude_id,
        )
    ).all()
    today = clock.today()
    return any(stamp is not None and clock.utc_to_local_date(stamp) == today for stamp in sent)


def deliver(session: Session, event_id: int) -> str:
    """
    Send one recorded event, or record why not. Returns a short outcome
    ("sent", "skipped:<reason>", "already_handled") for logs and tests.

    The row is locked for the duration, so two deliveries of the same event
    - a retried task, a duplicate in the queue - cannot both send it: the
    second waits, then finds it no longer pending.
    """
    event = session.exec(
        select(CoachEvent).where(CoachEvent.id == event_id).with_for_update()
    ).first()
    if event is None or event.status != STATUS_PENDING:
        session.commit()  # release the lock
        return "already_handled"

    user = session.get(User, event.user_id)
    if user is None or user.telegram_chat_id is None:
        return _skip(session, event, "no_chat")

    state, _ = resolve_state(session, user.telegram_chat_id)
    if state is not AccountState.ACTIVE:
        # Disconnected users asked us to stop; nothing unprompted reaches them.
        return _skip(session, event, "not_active")

    preference = PREFERENCE_FOR_KIND.get(event.kind)
    if preference is not None and not getattr(user, preference):
        return _skip(session, event, "turned_off")

    if event.kind in ONE_PER_DAY and _already_sent_today(session, user.id, event.kind, event.id):
        return _skip(session, event, "daily_limit")

    composer = COMPOSERS.get(event.kind)
    if composer is None:
        logger.error("No composer registered for coach event kind %r", event.kind)
        return _skip(session, event, "no_composer")

    if composer.uses_ai and budget_exhausted(session):
        return _skip(session, event, "ai_budget")

    text = composer.write(session, user, event)
    if not text:
        return _skip(session, event, "nothing_to_say")

    # Sent before marking: a send that fails leaves the event pending so the
    # task's retry can try again. The lock above keeps that retry from
    # overlapping a send that is still in progress.
    send_telegram_message(user.telegram_chat_id, text, reply_markup=TALK_KEYBOARD)

    # Into the history as the coach's own words, so "why?" right after it
    # is answered by a coach that knows what it just said.
    session.add(ChatMessage(user_id=user.id, role="assistant", content=text))
    event.status = STATUS_SENT
    event.sent_at = datetime.now(timezone.utc)
    session.add(event)
    session.commit()
    return "sent"
