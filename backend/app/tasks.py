"""
Celery task definitions - thin wrappers around app.services.garmin_sync
that add task-queue behavior (retries, being callable via .delay()).
The actual sync logic stays in the service layer and knows nothing
about Celery; this file only knows "how do I turn that logic into
something Celery can schedule/queue/retry".
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.celery_app import celery_app
from app.core.claude_client import (
    ClaudeAuthError,
    ClaudeNotConfiguredError,
    ClaudeOutOfCreditError,
    generate_daily_summary,
)
from app.core.config import settings
from app.core.garmin_client import GarminAuthError, GarminRateLimitError
from app.core.telegram_client import send_telegram_message
from app.db.session import engine
from app.models.garmin_account import GarminAccount
from app.models.user import User
from app.services.garmin_sync import sync_user_garmin_data
from app.services.metrics_view import format_metrics_snapshot

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Asia/Jerusalem")


@celery_app.task(bind=True, max_retries=3, default_retry_delay=300)
def sync_one_user_task(self, user_id: int, notify_on_success: bool = False) -> None:
    """
    One task per user - if one person's sync fails (expired token,
    Garmin temporarily down), it doesn't block or fail anyone else's,
    and Celery can retry just this task instead of redoing everyone.
    A fresh session is opened here rather than reusing a FastAPI request's
    session, since this task runs in a separate worker process with no
    request in progress at all.

    `notify_on_success` is set by the user-initiated paths (the 🔄 button,
    /sync) and left off for the twice-daily scheduled run. Without it a
    manual sync gave the user a "started in background" message and then
    permanent silence, which is indistinguishable from being stuck - while
    turning it on unconditionally would mean an unprompted message twice a
    day from the scheduler.
    """
    with Session(engine) as session:
        try:
            sync_user_garmin_data(session=session, user_id=user_id)
        except GarminRateLimitError as exc:
            user = session.get(User, user_id)
            if notify_on_success and user is not None and user.telegram_chat_id is not None:
                send_telegram_message(
                    user.telegram_chat_id,
                    "⏳ גרמין מגבילים כרגע את הבקשות. אנסה שוב אוטומטית בעוד כמה דקות.",
                )
            # Garmin is throttling, not a real failure - back off and retry later.
            raise self.retry(exc=exc, countdown=300)
        except GarminAuthError:
            # sync_user_garmin_data already deleted the now-invalid
            # GarminAccount (retrying wouldn't fix a genuinely rejected
            # token). If this user has a Telegram chat, tell them so they
            # aren't left silently un-synced until they happen to notice -
            # this fires for both a manual /sync and the scheduled sync.
            user = session.get(User, user_id)
            if user is not None and user.telegram_chat_id is not None:
                send_telegram_message(
                    user.telegram_chat_id,
                    "🔌 החיבור לגרמין פג תוקף. שלח /start כדי להתחבר מחדש.",
                )
            return

        if not notify_on_success:
            return

        user = session.get(User, user_id)
        if user is None or user.telegram_chat_id is None:
            return

        # Send the refreshed numbers rather than a bare "done": the reason
        # to press sync is to see current data, so making the user tap a
        # second button for it is a pointless extra step.
        send_telegram_message(
            user.telegram_chat_id,
            "✅ *הסנכרון הושלם*\n\n" + format_metrics_snapshot(session, user_id),
        )


SYNC_STAGGER_SECONDS = 8

# How much history to pull when a user first links. A month gives the AI
# coach real trends to reason about ("compared to last week...") instead of
# the two days the routine sync would leave it with, and is far enough back
# to pick up a VO2 max reading (Garmin only recalculates that after a
# qualifying activity, so recent days are often empty).
BACKFILL_DAYS = 30


@celery_app.task(bind=True, max_retries=2, default_retry_delay=600)
def backfill_user_history_task(self, user_id: int, days: int = BACKFILL_DAYS) -> None:
    """
    One-time deeper sync, run right after a user links their account.
    Paced at one request-group per second: 30 days is ~120 Garmin calls,
    and firing those as fast as possible is exactly what gets an IP
    throttled.
    """
    with Session(engine) as session:
        try:
            sync_user_garmin_data(
                session=session,
                user_id=user_id,
                days_back=days,
                pause_seconds=1.0,
                activity_limit=50,
            )
        except GarminRateLimitError as exc:
            raise self.retry(exc=exc, countdown=900)
        except GarminAuthError:
            # Token already invalidated and the user already notified by the
            # regular sync path - nothing useful to add here.
            return


@celery_app.task
def sync_all_linked_accounts() -> None:
    """
    Runs on Beat's daily schedule. Fans out: for every linked Garmin
    account, enqueues a separate sync_one_user_task instead of looping
    and doing the work directly here - that's what gives each user
    isolation and independent retries.

    Each task is staggered SYNC_STAGGER_SECONDS apart (via countdown, not
    just enqueue order - the worker's concurrency would otherwise still
    run several at once) rather than fired all at the same instant.
    In production, every user's Garmin login goes out from the same
    server IP - a burst of N simultaneous logins from one IP is exactly
    the pattern Garmin's anti-abuse system rate-limits, even though no
    single user did anything wrong.
    """
    with Session(engine) as session:
        # Disconnected accounts keep their token so reconnecting is easy,
        # but must not be synced - the user asked us to stop.
        user_ids = session.exec(
            select(GarminAccount.user_id).where(GarminAccount.disconnected_at.is_(None))
        ).all()

    for index, user_id in enumerate(user_ids):
        sync_one_user_task.apply_async(args=[user_id], countdown=index * SYNC_STAGGER_SECONDS)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120)
def send_daily_summary_task(self, user_id: int) -> None:
    """One user's end-of-day summary. Separate task per user for the same
    isolation reason as the sync fan-out: one failed Claude call or blocked
    chat must not stop everyone else's summary."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None or user.telegram_chat_id is None:
            return

        try:
            summary = generate_daily_summary(session, user)
        except (ClaudeNotConfiguredError, ClaudeAuthError, ClaudeOutOfCreditError) as exc:
            # None of these get better by trying again: a missing or
            # rejected key and an empty credit balance all need a human.
            # Retrying just burns queue slots until it gives up.
            logger.warning("Daily summary skipped for user %s - %s", user_id, type(exc).__name__)
            return
        except Exception as exc:
            logger.exception("Daily summary generation failed for user %s", user_id)
            raise self.retry(exc=exc)

        # None means there was genuinely nothing to summarize - better to
        # stay quiet than to send an empty-handed daily message.
        if summary:
            send_telegram_message(user.telegram_chat_id, summary)


@celery_app.task
def dispatch_daily_summaries() -> None:
    """
    Runs hourly from Beat and fans out to whoever asked for a summary at
    *this* hour. Scheduling per-user times this way (rather than one Beat
    entry per possible hour) keeps the schedule static while letting each
    user pick their own time and change it whenever they want.
    """
    if not settings.is_claude_configured:
        logger.info("Skipping daily summaries - ANTHROPIC_API_KEY is not configured")
        return

    current_hour = datetime.now(LOCAL_TZ).hour

    with Session(engine) as session:
        user_ids = session.exec(
            select(User.id)
            .join(GarminAccount, GarminAccount.user_id == User.id)
            .where(
                User.daily_summary_hour == current_hour,
                User.telegram_chat_id.is_not(None),
                # Same reason as the sync fan-out: a disconnected user
                # asked us to stop, so no unprompted daily message either.
                GarminAccount.disconnected_at.is_(None),
            )
        ).all()

    for index, user_id in enumerate(user_ids):
        send_daily_summary_task.apply_async(args=[user_id], countdown=index * 5)
