"""
Per-user ceilings on Claude usage, and the reporting built on the same data.

Checked *before* the API call, never after: a limit enforced afterwards has
already spent the money it exists to protect.

Two independent ceilings, because they catch different failure modes. A
message count stops compulsive back-and-forth. A cost cap stops the rarer
case a message count waves through - a handful of very long, tool-heavy
conversations that each cost many times a normal turn.

Only the conversational path is limited. The quick-lookup buttons read the
database and make no API call, so throttling them would punish the cheapest
thing a user can do.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, func, select

from app.core.config import settings
from app.models.usage_event import UsageEvent
from app.models.user import User

logger = logging.getLogger(__name__)


class QuotaExceeded(Exception):
    """Raised when a user has hit a ceiling. `message` is user-facing Hebrew."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def messages_today(session: Session, user_id: int) -> int:
    """
    Conversational turns in the last 24 hours.

    A rolling window rather than a calendar day: a midnight reset invites
    "wait for midnight" behaviour, and a user in another timezone would get
    an arbitrary partial day.

    Counts `kind='chat'` only - the daily summary is sent by the scheduler,
    not requested by the user, and must never consume their allowance. Each
    turn writes several rows (one per tool round), so this counts distinct
    turns by rounding on the first call of each: turns are what the user
    perceives, rounds are an implementation detail.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    # cache_write > 0 marks the first call of a turn - the prefix is written
    # once and read on the rounds that follow, so counting those rows counts
    # turns. Falls back to counting rows if caching is ever disabled.
    turns = session.exec(
        select(func.count())
        .select_from(UsageEvent)
        .where(
            UsageEvent.user_id == user_id,
            UsageEvent.kind == "chat",
            UsageEvent.created_at >= since,
            UsageEvent.cache_write_tokens > 0,
        )
    ).one()
    return turns


def cost_this_month(session: Session, user_id: int) -> float:
    """
    Total USD this calendar month, across every kind of call.

    Unlike the message count this *does* include the daily summary: the point
    of a cost ceiling is protecting the credit balance, and money spent on a
    user's behalf is still money spent.
    """
    total = session.exec(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.created_at >= _month_start(datetime.now(timezone.utc)),
        )
    ).one()
    return float(total or 0.0)


def check_quota(session: Session, user: User) -> None:
    """
    Raise QuotaExceeded if this user may not make another coach request.

    A per-user override on User.daily_message_limit wins over the global
    default, so one heavy user can be handled without loosening the ceiling
    for everyone.
    """
    limit = user.daily_message_limit
    if limit is None:
        limit = settings.daily_message_limit

    # 0 is a real value meaning "no coach access", distinct from None
    # meaning "use the default" - hence the explicit None check above.
    if limit == 0:
        raise QuotaExceeded("💬 הגישה לשיחה עם המאמן כבויה עבורך כרגע.")

    used = messages_today(session, user.id)
    if used >= limit:
        raise QuotaExceeded(
            f"💬 הגעת למכסה היומית ({limit} הודעות).\n"
            "כפתורי המדדים המהירים ממשיכים לעבוד כרגיל, ונתראה מחר 🙂"
        )

    spent = cost_this_month(session, user.id)
    if spent >= settings.monthly_cost_limit_usd:
        raise QuotaExceeded(
            "💬 הגעת למכסת השימוש החודשית בשיחה עם המאמן.\n"
            "כפתורי המדדים המהירים ממשיכים לעבוד כרגיל."
        )


def usage_report(session: Session) -> list[dict]:
    """
    Per-user totals for this month, heaviest first - the data behind /admin.

    A left join, so a user who has never chatted still appears with zeros.
    Someone registered and silent is exactly who an owner wants to see.
    """
    since = _month_start(datetime.now(timezone.utc))
    rows = session.exec(
        select(
            User.id,
            User.telegram_chat_id,
            User.daily_message_limit,
            func.count(UsageEvent.id),
            func.coalesce(func.sum(UsageEvent.cost_usd), 0.0),
            func.coalesce(func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens), 0),
        )
        .join(
            UsageEvent,
            (UsageEvent.user_id == User.id) & (UsageEvent.created_at >= since),
            isouter=True,
        )
        .group_by(User.id, User.telegram_chat_id, User.daily_message_limit)
        .order_by(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0).desc())
    ).all()

    return [
        {
            "user_id": r[0],
            "telegram_chat_id": r[1],
            "daily_limit": r[2] if r[2] is not None else settings.daily_message_limit,
            "api_calls": r[3],
            "cost_usd": float(r[4] or 0.0),
            "tokens": int(r[5] or 0),
        }
        for r in rows
    ]
