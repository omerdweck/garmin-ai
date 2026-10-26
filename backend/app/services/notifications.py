"""
The notifications screen: what gets recorded, and how the Mini App reads it.

Recording
    record() is called right after the bot sends something nobody asked
    for. Only *unprompted* messages belong here - the post-workout and
    anomaly messages (coach_events.deliver), the daily and weekly summaries,
    a new personal record, reminders and warnings from scheduled tasks.
    Replies to the user's own messages and buttons are not: they are the
    conversation, already on screen in Telegram, and filing them would bury
    the few messages the bell exists for.

    A notification that fails to record must never fail the send it
    describes - a retried task would send the message a second time.
    record_safely() is the form for callers outside a transaction of their
    own; deliver() records inside its own commit instead.

Reading
    Newest first, paged by id ("before" the last id the page has), the same
    shape as the workout list. Written for the Mini App's read-only role:
    the only write it makes is read_at, the one column it is granted.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.core import clock
from app.core.config import settings
from app.models.chat_message import ChatMessage
from app.models.coach_event import KIND_ANOMALY, KIND_POST_WORKOUT, STATUS_SENT, CoachEvent
from app.models.metric_alert import MetricAlert
from app.models.notification import Notification
from app.services import anomaly_rules as rules

logger = logging.getLogger(__name__)

# --- links -------------------------------------------------------------------

# Which card on the health tab explains each anomaly rule. The ids are the
# DOM ids the dashboard gives its cards: metric-<the metric's key in
# Health.tsx>, and metric-sleep for the sleep card. The two workout rules
# have no single card to point at; the workouts tab is where that story is.
_ANOMALY_TARGET = {
    rules.RULE_HRV: ("health", "metric-avg_hrv"),
    rules.RULE_RHR: ("health", "metric-resting_heart_rate"),
    rules.RULE_BODY_BATTERY: ("health", "metric-body_battery_high"),
    rules.RULE_SLEEP: ("health", "metric-sleep"),
    rules.RULE_EASY_RUN: ("workouts", None),
    rules.RULE_LOAD: ("workouts", None),
}


def tab_link(tab: str, focus: Optional[str] = None) -> dict:
    link = {"type": "tab", "tab": tab}
    if focus:
        link["focus"] = focus
    return link


def link_for_event(session: Session, event: CoachEvent) -> Optional[dict]:
    """Where tapping a coach event's notification goes."""
    payload = event.payload or {}
    if event.kind == KIND_POST_WORKOUT and payload.get("activity_id"):
        return {"type": "activity", "id": int(payload["activity_id"])}
    if event.kind == KIND_ANOMALY:
        # The first alert in the message is the one it opens with, so it is
        # the one the tap should land on.
        ids = payload.get("alert_ids") or []
        rule = session.exec(
            select(MetricAlert.rule)
            .where(col(MetricAlert.id).in_(ids), MetricAlert.user_id == event.user_id)
            .order_by(col(MetricAlert.id))
        ).first() if ids else None
        tab, focus = _ANOMALY_TARGET.get(rule, ("health", None))
        return tab_link(tab, focus)
    return None


# --- recording ---------------------------------------------------------------


def record(
    session: Session,
    user_id: int,
    kind: str,
    text: str,
    source_key: str,
    link: Optional[dict] = None,
    *,
    created_at: Optional[datetime] = None,
    read_at: Optional[datetime] = None,
) -> None:
    """
    Adds the notification to the session's transaction; the caller commits.
    ON CONFLICT DO NOTHING, for the same reason as coach_events.record_event:
    a check-then-insert would let two retries both pass the check.
    """
    session.execute(
        insert(Notification)
        .values(
            user_id=user_id,
            kind=kind,
            text=text,
            link=link,
            source_key=source_key,
            created_at=created_at or clock.now_utc(),
            read_at=read_at,
        )
        .on_conflict_do_nothing(constraint="uq_notification_user_source")
    )


def record_safely(
    session: Session, user_id: int, kind: str, text: str, source_key: str, link: Optional[dict] = None
) -> None:
    """record() and commit, for a message that has already gone out. Never raises."""
    try:
        record(session, user_id, kind, text, source_key, link)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Could not record a %s notification for user %s", kind, user_id)


# --- the backfill ------------------------------------------------------------

# How far back, and how many. The screen is for recent things; a user's
# first look at it needs the last weeks, not everything since they joined.
BACKFILL_LIMIT = 100
# deliver() stores the chat_message a moment before it stamps sent_at -
# milliseconds apart in practice. A generous window still cannot catch the
# wrong message: the coach does not answer anything within a minute of an
# unprompted message it is still in the middle of sending.
MATCH_WINDOW = timedelta(minutes=1)

BACKFILL_KEY = "notifications-backfill:{}"
# Long enough that opening the app a hundred times queues it once; short
# enough that a backfill lost to a worker restart is tried again.
BACKFILL_KEY_SECONDS = 24 * 3600


def backfill_from_coach_events(session: Session, user_id: int) -> int:
    """
    Files the coach messages sent before this table existed, so the screen
    is not empty on its first day. Returns how many it added.

    Only post-workout and anomaly messages can be recovered: they are the
    ones with a coach_event row (sent_at says when) and their exact text in
    chat_message (deliver() stores it there). The text is matched by user,
    role and time - the closest assistant message within a minute of
    sent_at. Events with no such message are skipped rather than guessed at.

    Cheap by construction: the newest BACKFILL_LIMIT sent events that have
    no notification yet, one indexed lookup each, run once per user from a
    worker (the Mini App's role cannot read chat_message). Idempotent: an
    event already filed - by this or by deliver() - is excluded up front,
    and the unique key would stop a duplicate anyway.

    Filed as already read, at their original time: the user saw them in
    Telegram when they arrived, and a badge of 40 on day one would only
    teach them to ignore it.
    """
    filed = select(Notification.source_key).where(Notification.user_id == user_id)
    events = session.exec(
        select(CoachEvent)
        .where(
            CoachEvent.user_id == user_id,
            CoachEvent.status == STATUS_SENT,
            col(CoachEvent.sent_at).is_not(None),
            func.concat("coach_event:", CoachEvent.id).not_in(filed),
        )
        .order_by(col(CoachEvent.sent_at).desc())
        .limit(BACKFILL_LIMIT)
    ).all()

    added = 0
    for event in events:
        sent_at = event.sent_at
        distance = func.abs(func.extract("epoch", ChatMessage.created_at - sent_at))
        text = session.exec(
            select(ChatMessage.content)
            .where(
                ChatMessage.user_id == user_id,
                ChatMessage.role == "assistant",
                ChatMessage.created_at >= sent_at - MATCH_WINDOW,
                ChatMessage.created_at <= sent_at + MATCH_WINDOW,
            )
            .order_by(distance)
        ).first()
        if not text:
            continue
        record(
            session, user_id, event.kind, text, source_key_for_event(event.id),
            link_for_event(session, event), created_at=sent_at, read_at=sent_at,
        )
        added += 1
    session.commit()
    return added


def source_key_for_event(event_id: int) -> str:
    return f"coach_event:{event_id}"


def _redis() -> redis.Redis:
    return redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0, socket_connect_timeout=2)


def request_backfill(user_id: int) -> bool:
    """
    Called by the Mini App when it asks for notifications. Queues the
    backfill at most once a day per user (SET NX), on a worker that can
    read chat_message. True if it was queued just now - the page then asks
    again a moment later rather than showing an empty screen as final.
    """
    try:
        if not _redis().set(BACKFILL_KEY.format(user_id), "1", nx=True, ex=BACKFILL_KEY_SECONDS):
            return False
        from app.celery_app import celery_app

        celery_app.send_task("app.tasks.backfill_notifications_task", args=[user_id])
        return True
    except Exception:
        # A dashboard that cannot reach Redis still shows what it has.
        logger.warning("Could not queue the notifications backfill for user %s", user_id, exc_info=True)
        return False


# --- reading -----------------------------------------------------------------


def _iso(stamp: Optional[datetime]) -> Optional[str]:
    # Stored naive UTC (see clock.py); said explicitly so the page does not
    # read it as local time.
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.isoformat()


def unread_count(session: Session, user_id: int) -> int:
    return session.exec(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user_id, col(Notification.read_at).is_(None)
        )
    ).one()


def page(session: Session, user_id: int, before: Optional[int], limit: int) -> dict:
    query = select(Notification).where(Notification.user_id == user_id)
    if before is not None:
        query = query.where(Notification.id < before)
    # One extra row says whether there is a next page without a count query.
    rows = session.exec(query.order_by(col(Notification.id).desc()).limit(limit + 1)).all()
    return {
        "items": [
            {
                "id": row.id,
                "kind": row.kind,
                "text": row.text,
                "link": row.link,
                "created_at": _iso(row.created_at),
                "read": row.read_at is not None,
            }
            for row in rows[:limit]
        ],
        "unread": unread_count(session, user_id),
        "more": len(rows) > limit,
    }


def mark_read(session: Session, user_id: int, ids: Optional[list[int]] = None, everything: bool = False) -> int:
    """Marks the user's own notifications read; anyone else's ids are simply not matched."""
    if not everything and not ids:
        return unread_count(session, user_id)
    statement = update(Notification).where(
        Notification.user_id == user_id, col(Notification.read_at).is_(None)
    )
    if not everything:
        statement = statement.where(col(Notification.id).in_(ids))
    session.execute(statement.values(read_at=clock.now_utc()))
    session.commit()
    return unread_count(session, user_id)
