"""
The system-wide monthly ceiling on scheduled and proactive AI work.

Per-user quotas (usage_limits.py) protect against one person chatting too
much. They cannot protect against the system itself: summaries and coach
messages are sent by the scheduler, so a bug that fires them in a loop would
spend without any user doing anything. This is the guard for that.

Conversations are deliberately not stopped here - they already have their
own per-user ceilings, and a user mid-conversation should not hit a wall
because of a background job.
"""

import logging
from datetime import datetime, timezone

import redis
from sqlmodel import Session, func, select

from app.core import clock
from app.core.config import settings
from app.models.usage_event import UsageEvent

logger = logging.getLogger(__name__)


def _month_start_utc() -> datetime:
    local = clock.now_local()
    start_local = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc)


def system_cost_this_month(session: Session) -> float:
    total = session.exec(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
            UsageEvent.created_at >= _month_start_utc()
        )
    ).one()
    return float(total or 0.0)


def budget_exhausted(session: Session) -> bool:
    exhausted = system_cost_this_month(session) >= settings.ai_monthly_budget_usd
    if exhausted:
        _alert_owner_once()
    return exhausted


def _alert_owner_once() -> None:
    """
    One message to the owner per month, not one per blocked summary. The
    marker lives in Redis so every worker process sees the same one.
    """
    if not settings.is_admin_configured:
        return
    month = clock.now_local().strftime("%Y-%m")
    client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0)
    try:
        first = client.set(f"ai-budget-alert:{month}", "1", nx=True, ex=40 * 24 * 3600)
    except redis.RedisError:
        logger.warning("Could not check the budget-alert marker", exc_info=True)
        return
    if not first:
        return

    # Imported here: telegram_client pulls in python-telegram-bot, which the
    # rest of this module has no use for.
    from app.core.telegram_client import send_telegram_message

    send_telegram_message(
        settings.admin_chat_id,
        f"💸 *תקציב ה-AI החודשי נוצל* ({settings.ai_monthly_budget_usd:.0f}$)\n\n"
        "סיכומים והודעות יזומות של המאמן נעצרו עד תחילת החודש הבא.\n"
        "שיחות עם המאמן ממשיכות לעבוד כרגיל.\n\n"
        "להגדלה: `AI_MONTHLY_BUDGET_USD` ב-.env",
    )
