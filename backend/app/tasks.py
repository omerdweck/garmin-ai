"""
Celery task definitions - thin wrappers around app.services.garmin_sync
that add task-queue behavior (retries, being callable via .delay()).
The actual sync logic stays in the service layer and knows nothing
about Celery; this file only knows "how do I turn that logic into
something Celery can schedule/queue/retry".
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlmodel import Session, select

from app.core import clock
from app.celery_app import celery_app
from app.core.claude_client import (
    ClaudeAuthError,
    ClaudeNotConfiguredError,
    ClaudeOutOfCreditError,
    generate_daily_summary,
    generate_weekly_summary,
)
from app.core.config import settings
from app.core.crypto import decrypt
from app.core.garmin_client import (
    GarminAuthError,
    GarminRateLimitError,
    GarminRequestError,
    get_watch_last_upload,
    resume_garmin_session,
)
from app.core.telegram_client import send_telegram_document, send_telegram_message
from app.db.session import engine
from app.models.garmin_account import GarminAccount
from app.models import notification as notification_kinds
from app.models.plan_session import week_index
from app.models.user import User
from app.services.calorie_tracking import (
    ABANDON_AFTER_DAYS,
    consumed_on,
    days_since_last_entry,
)
from app.services.garmin_sync import sync_user_garmin_data
from app.services.ai_budget import budget_exhausted
from app.services import anomalies
from app.services import anomaly_message  # noqa: F401 - registers the alert composer
from app.services.coach_events import deliver as deliver_coach_event
from app.services.garmin_throttle import is_throttled as garmin_is_throttled
from app.services.garmin_throttle import mark_throttled as mark_garmin_throttled
from app.services.post_workout import (
    fresh_activities_since,
    latest_garmin_activity_id,
    record_post_workout_events,
)
from app.services.records import format_broken_records
from app.services import workouts as workout_service
from app.services.sync_lock import release as release_sync_lock
from app.services.sync_lock import user_sync_lock
from app.services.sync_lock import named_lock
from app.services import history_import
from app.services import notifications
from app.models.history_import import STATUS_PAUSED, HistoryImport
from app.services.metrics_view import (
    WATCH_STALE_HOURS,
    format_metrics_snapshot,
    format_watch_sync_line,
)

logger = logging.getLogger(__name__)

def _warn_if_watch_stale(session: Session, user: User) -> None:
    """
    One warning per staleness episode, sent from the scheduled sync.

    "Episode" rather than "run" is the whole point: the schedule fires twice
    a day, so a per-run warning would send fourteen identical messages over a
    forgotten week. Comparing the last warning against the last upload means
    the counter resets by itself the moment the watch syncs again.
    """
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()
    if account is None or account.watch_last_upload_at is None:
        return

    stamp = account.watch_last_upload_at
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) - stamp < timedelta(hours=WATCH_STALE_HOURS):
        return

    already = account.watch_stale_notified_at
    if already is not None:
        if already.tzinfo is None:
            already = already.replace(tzinfo=timezone.utc)
        if already > stamp:
            return  # already warned about this same episode

    text = format_watch_sync_line(session, user.id).strip()
    send_telegram_message(user.telegram_chat_id, text)
    account.watch_stale_notified_at = datetime.now(timezone.utc)
    session.add(account)
    session.commit()
    # Keyed by the upload it is about: one warning per episode, as above.
    notifications.record_safely(
        session, user.id, notification_kinds.KIND_WATCH, text, f"watch_stale:{stamp.isoformat()}"
    )


# Hard ceilings per task. A Garmin call that never returns used to hold a
# worker process forever; now the soft limit raises inside the task (so it
# can clean up) and the hard limit kills it outright shortly after.
SYNC_SOFT_LIMIT, SYNC_HARD_LIMIT = 240, 300
BACKFILL_SOFT_LIMIT, BACKFILL_HARD_LIMIT = 900, 960
SUMMARY_SOFT_LIMIT, SUMMARY_HARD_LIMIT = 150, 180
SMALL_SOFT_LIMIT, SMALL_HARD_LIMIT = 60, 90

# The sync lock outlives the hard limit, so it can never expire while the
# sync holding it is still running.
SYNC_LOCK_SECONDS = BACKFILL_HARD_LIMIT + 60


# acks_late: the message is acknowledged after the task finishes rather than
# when it starts, so a sync whose worker dies mid-way is delivered again.
# Safe only because a sync is an upsert - running it twice changes nothing.
# Never set on tasks that send messages: there a redelivery is a duplicate.
@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
    soft_time_limit=SYNC_SOFT_LIMIT,
    time_limit=SYNC_HARD_LIMIT,
)
def sync_one_user_task(self, user_id: int, notify_on_success: bool = False) -> None:
    lock = user_sync_lock(user_id, SYNC_LOCK_SECONDS)
    if not lock.acquire(blocking=False):
        if notify_on_success:
            # The user pressed 🔄 while another sync of theirs is running.
            # Waiting for it, then reporting, beats both a second parallel
            # sync and a "try again later" they would have to act on.
            raise self.retry(countdown=20, max_retries=6)
        return  # scheduled run: the sync already in progress covers it
    try:
        _sync_one_user(self, user_id, notify_on_success)
    finally:
        release_sync_lock(lock)


def _sync_one_user(self, user_id: int, notify_on_success: bool = False) -> None:
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
        # Read before syncing so the message can tell "the watch uploaded
        # something new" apart from "nothing has arrived since last time".
        # Without that distinction a user who follows the instruction gets
        # the identical warning back and concludes the bot is broken.
        account_before = session.exec(
            select(GarminAccount).where(GarminAccount.user_id == user_id)
        ).first()
        upload_before = account_before.watch_last_upload_at if account_before else None
        # Everything with a higher id after the sync is a workout it brought in.
        last_activity_before = latest_garmin_activity_id(session, user_id)

        try:
            broken_records = sync_user_garmin_data(session=session, user_id=user_id)
        except GarminRateLimitError as exc:
            mark_garmin_throttled()
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
                text = "🔌 החיבור לגרמין פג תוקף. שלח /start כדי להתחבר מחדש."
                send_telegram_message(user.telegram_chat_id, text)
                notifications.record_safely(
                    session, user.id, notification_kinds.KIND_ACCOUNT, text,
                    f"garmin_auth:{clock.today().isoformat()}",
                )
            return

        user = session.get(User, user_id)
        if user is None or user.telegram_chat_id is None:
            return

        # Before the notify_on_success split, deliberately: a new personal
        # record is worth telling someone about whether they pressed sync or
        # the scheduler found it. It is also self-limiting - it can only fire
        # on the one run that first sees the better number, so it cannot
        # become the twice-daily noise the silent-scheduled-run rule exists
        # to prevent.
        # A fresh workout gets its own message - and new records go inside
        # it rather than as a second message right next to it.
        new_workouts = fresh_activities_since(session, user_id, last_activity_before)
        # The workout page's data, fetched now rather than when the workout is
        # first opened - the most likely moment to open it is right after the
        # post-workout message arrives.
        for workout in new_workouts[:5]:
            fetch_activity_detail_task.apply_async(args=[workout.id], countdown=5)
        if new_workouts and user.notify_post_workout:
            for event in record_post_workout_events(session, user, new_workouts, broken_records):
                deliver_coach_event_task.delay(event.id)
            broken_records = []

        if broken_records:
            text = format_broken_records(broken_records)
            send_telegram_message(user.telegram_chat_id, text)
            # The record itself is the key: the same number cannot be broken twice.
            key = "|".join(
                f"{r.get('sport')}:{r.get('record_type')}:{r.get('value')}" for r in broken_records
            )
            notifications.record_safely(
                session, user.id, notification_kinds.KIND_RECORD, text, f"records:{key}",
                notifications.tab_link("records"),
            )

        # Anomalies are checked on every sync - it is a few queries over data
        # already stored - and announced through the same pipeline, which
        # applies the user's switch and the one-per-day limit. A failure here
        # must never fail the sync that already succeeded.
        try:
            alert_event = anomalies.check_user(session, user_id)
        except Exception:
            session.rollback()
            logger.exception("Anomaly check failed for user %s", user_id)
            alert_event = None
        if alert_event is not None:
            deliver_coach_event_task.delay(alert_event.id)

        if not notify_on_success:
            # Scheduled run: normally silent. The one thing worth an
            # unprompted message is a watch that hasn't uploaded, because
            # that is the only cause of stale data the user can actually fix
            # - and staying quiet leaves them believing the bot is broken.
            _warn_if_watch_stale(session, user)
            return

        # Send the refreshed numbers rather than a bare "done": the reason
        # to press sync is to see current data, so making the user tap a
        # second button for it is a pointless extra step. The watch line goes
        # first: if it is stale, it explains everything below it.
        account_after = session.exec(
            select(GarminAccount).where(GarminAccount.user_id == user_id)
        ).first()
        upload_after = account_after.watch_last_upload_at if account_after else None
        nothing_new = upload_before is not None and upload_before == upload_after

        send_telegram_message(
            user.telegram_chat_id,
            format_watch_sync_line(session, user_id, unchanged_since_last_try=nothing_new)
            + "✅ *הסנכרון הושלם*\n\n"
            + format_metrics_snapshot(session, user_id),
        )


SYNC_STAGGER_SECONDS = 8

# How much history to pull when a user first links. A month gives the AI
# coach real trends to reason about ("compared to last week...") instead of
# the two days the routine sync would leave it with, and is far enough back
# to pick up a VO2 max reading (Garmin only recalculates that after a
# qualifying activity, so recent days are often empty).
BACKFILL_DAYS = 30


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=600,
    acks_late=True,
    soft_time_limit=BACKFILL_SOFT_LIMIT,
    time_limit=BACKFILL_HARD_LIMIT,
)
def backfill_user_history_task(self, user_id: int, days: int = BACKFILL_DAYS) -> None:
    lock = user_sync_lock(user_id, SYNC_LOCK_SECONDS)
    if not lock.acquire(blocking=False):
        # Linking starts the quick sync and the backfill together, so this is
        # the normal case, not an edge one: wait for the quick sync to finish.
        # Skipping would silently lose the month of history.
        raise self.retry(countdown=45, max_retries=8)
    try:
        _backfill_user_history(self, user_id, days)
    finally:
        release_sync_lock(lock)


def _backfill_user_history(self, user_id: int, days: int = BACKFILL_DAYS) -> None:
    """
    One-time deeper sync, run right after a user links their account.
    Paced at one request-group per second: 30 days is ~120 Garmin calls,
    and firing those as fast as possible is exactly what gets an IP
    throttled.
    """
    with Session(engine) as session:
        try:
            # Return value ignored on purpose: on a first link every record
            # is a first sight, which _apply_records stores without reporting.
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


# --- One workout, second by second -------------------------------------------
# Run right after a sync finds a new workout, and when the Mini App opens a
# workout that doesn't have it yet. The Redis key is set by whoever enqueues
# (so a page opened twice asks once) and cleared here when done.

DETAIL_PENDING_KEY = "activity-detail-pending:{}"
DETAIL_PENDING_SECONDS = 180


def detail_pending_key(activity_id: int) -> str:
    return DETAIL_PENDING_KEY.format(activity_id)


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def fetch_activity_detail_task(activity_id: int) -> None:
    from garminconnect.exceptions import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

    from app.models.activity import Activity
    from app.services.garmin_sync import fetch_activity_detail
    from app.services.sync_lock import _redis

    try:
        if garmin_is_throttled():
            return
        with Session(engine) as session:
            activity = session.get(Activity, activity_id)
            if activity is None or activity.detail is not None or activity.garmin_activity_id is None:
                return
            account = session.exec(
                select(GarminAccount).where(GarminAccount.user_id == activity.user_id)
            ).first()
            if account is None or account.disconnected_at is not None:
                return
            try:
                garmin = resume_garmin_session(decrypt(account.encrypted_token))
                fetch_activity_detail(session, activity, garmin)
            except (GarminRateLimitError, GarminConnectTooManyRequestsError):
                mark_garmin_throttled()
            except (GarminAuthError, GarminConnectAuthenticationError):
                # The routine sync handles a rejected login and tells the user.
                pass
            except Exception:
                session.rollback()
                logger.exception("Fetching detail for activity %s failed", activity_id)
    finally:
        try:
            _redis().delete(detail_pending_key(activity_id))
        except Exception:
            pass


# --- The one-time import of each user's past year ---------------------------
# Runs every 10 minutes all day; does nothing outside the night window. One
# chunk at a time for the whole server (the global lock), because the point
# is a trickle of requests from this IP, not one trickle per user.


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def dispatch_history_import() -> None:
    if settings.history_import_scope == "off":
        return
    if not history_import.in_night_window(
        clock.now_local().hour, settings.history_import_start_hour, settings.history_import_end_hour
    ):
        return
    if garmin_is_throttled():
        return
    with Session(engine) as session:
        user_ids = history_import.eligible_user_ids(
            session, settings.history_import_scope, settings.admin_chat_id
        )
        for user_id in user_ids:
            history_import.ensure_import(session, user_id, clock.today(), settings.history_import_days)
        history_import.resume_paused(session, user_ids)
        next_user = history_import.next_user_to_import(session, user_ids)
    if next_user is not None:
        import_history_chunk_task.delay(next_user)


@celery_app.task(soft_time_limit=BACKFILL_SOFT_LIMIT, time_limit=BACKFILL_HARD_LIMIT)
def import_history_chunk_task(user_id: int) -> None:
    job_lock = named_lock("history-import", BACKFILL_HARD_LIMIT + 60)
    if not job_lock.acquire(blocking=False):
        return  # the previous chunk is still running; the next tick tries again
    try:
        user_lock = user_sync_lock(user_id, SYNC_LOCK_SECONDS)
        if not user_lock.acquire(blocking=False):
            return  # this user's sync is running; never two writers at once
        try:
            _import_history_chunk(user_id)
        finally:
            release_sync_lock(user_lock)
    finally:
        release_sync_lock(job_lock)


def _import_history_chunk(user_id: int) -> None:
    with Session(engine) as session:
        record = session.exec(select(HistoryImport).where(HistoryImport.user_id == user_id)).first()
        account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user_id)).first()
        if record is None or account is None or account.disconnected_at is not None:
            return
        try:
            garmin_session = resume_garmin_session(decrypt(account.encrypted_token))
            history_import.run_chunk(
                session,
                record,
                garmin_session,
                chunk_days=settings.history_import_chunk_days,
                pause_seconds=settings.history_import_pause_seconds,
            )
        except (GarminRateLimitError, history_import.ImportThrottled):
            # Everyone's Garmin traffic shares this switch: the routine syncs
            # and upload checks back off too, and the import waits it out.
            mark_garmin_throttled()
        except GarminAuthError:
            record.status = STATUS_PAUSED
            session.add(record)
            session.commit()


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
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


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120, soft_time_limit=SUMMARY_SOFT_LIMIT, time_limit=SUMMARY_HARD_LIMIT)
def send_daily_summary_task(self, user_id: int) -> None:
    """One user's end-of-day summary. Separate task per user for the same
    isolation reason as the sync fan-out: one failed Claude call or blocked
    chat must not stop everyone else's summary."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None or user.telegram_chat_id is None:
            return
        if budget_exhausted(session):
            logger.warning("Daily summary skipped for user %s - monthly AI budget reached", user_id)
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
            notifications.record_safely(
                session, user.id, notification_kinds.KIND_DAILY_SUMMARY, summary,
                f"daily_summary:{clock.today().isoformat()}", notifications.tab_link("today"),
            )


@celery_app.task(bind=True, max_retries=2, default_retry_delay=120, soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def send_calorie_reminder_task(self, user_id: int) -> None:
    """
    One user's nudge to log what they ate - or, after a long enough silence,
    the offer to stop being nudged.

    The abandonment check lives here rather than in a separate job because
    the two are mutually exclusive: someone who has not logged in five days
    should be asked whether to stop, not reminded for the sixth time.
    """
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None or user.telegram_chat_id is None:
            return
        if user.daily_calorie_target is None:
            # Turned tracking off between the dispatch and now.
            return

        silent_days = days_since_last_entry(session, user.id)

        if silent_days is not None and silent_days >= ABANDON_AFTER_DAYS:
            # Asked once per episode, not once per reminder. Comparing the
            # prompt against the last entry - the same shape as the stale
            # watch warning - means the counter resets by itself the moment
            # the user logs something again.
            if user.calorie_abandon_prompted_at is not None:
                return

            text = (
                f"🍽 *לא רשמת קלוריות כבר {silent_days} ימים*\n\n"
                "אם המעקב לא מסתדר לך - אפשר לכבות אותו, והרישומים יישמרו.\n"
                "אם כן, פשוט תמשיך להזין ואפסיק לשאול 🙂\n\n"
                "לכיבוי: 🍽 קלוריות ← ⚙️ הגדרות מעקב"
            )
            send_telegram_message(user.telegram_chat_id, text)
            user.calorie_abandon_prompted_at = datetime.now(timezone.utc)
            session.add(user)
            session.commit()
            notifications.record_safely(
                session, user.id, notification_kinds.KIND_REMINDER, text,
                f"calorie_abandon:{clock.today().isoformat()}",
            )
            return

        consumed = consumed_on(session, user.id)
        target = user.daily_calorie_target
        if consumed:
            body = (
                f"🍽 עד עכשיו רשמת *{consumed:,}* מתוך *{target:,}* קלוריות.\n"
                "אכלת עוד משהו? לחץ 🍽 קלוריות ← ➕ הוספת ארוחה"
            )
        else:
            body = (
                f"🍽 *תזכורת* - עוד לא רשמת קלוריות היום.\n"
                f"היעד שלך: *{target:,}*\n\n"
                "לחץ 🍽 קלוריות ← ➕ הוספת ארוחה"
            )
        send_telegram_message(user.telegram_chat_id, body)
        # Up to three a day, one per reminder hour - the hour tells them apart.
        notifications.record_safely(
            session, user.id, notification_kinds.KIND_REMINDER, body,
            f"calorie_reminder:{clock.today().isoformat()}:{clock.now_local().hour}",
        )


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def dispatch_calorie_reminders() -> None:
    """
    Hourly fan-out, same shape as dispatch_daily_summaries.

    A user can have up to three reminder hours, so the same person may be
    selected by three different hourly runs - which is the point, and is why
    the hour is matched against any of the three columns rather than one.
    """
    current_hour = clock.now_local().hour

    with Session(engine) as session:
        user_ids = session.exec(
            select(User.id).where(
                User.daily_calorie_target.is_not(None),
                User.telegram_chat_id.is_not(None),
                or_(
                    User.calorie_reminder_hour_1 == current_hour,
                    User.calorie_reminder_hour_2 == current_hour,
                    User.calorie_reminder_hour_3 == current_hour,
                ),
            )
        ).all()

    for index, user_id in enumerate(user_ids):
        send_calorie_reminder_task.apply_async(args=[user_id], countdown=index * 5)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=300, soft_time_limit=SUMMARY_SOFT_LIMIT, time_limit=SUMMARY_HARD_LIMIT)
def send_weekly_summary_task(self, user_id: int) -> None:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None or user.telegram_chat_id is None:
            return
        if budget_exhausted(session):
            logger.warning("Weekly summary skipped for user %s - monthly AI budget reached", user_id)
            return
        try:
            summary = generate_weekly_summary(session, user)
        except (ClaudeNotConfiguredError, ClaudeAuthError, ClaudeOutOfCreditError) as exc:
            # None of these improve by retrying - a missing key, a rejected
            # one and an empty balance all need a human.
            logger.warning("Weekly summary skipped for user %s - %s", user_id, type(exc).__name__)
            return
        except Exception as exc:
            logger.exception("Weekly summary failed for user %s", user_id)
            raise self.retry(exc=exc)

        # None means the week was genuinely empty - better silence than a
        # summary of nothing.
        if summary:
            send_telegram_message(user.telegram_chat_id, summary)
            notifications.record_safely(
                session, user.id, notification_kinds.KIND_WEEKLY_REPORT, summary,
                f"weekly_report:{clock.today().isoformat()}", notifications.tab_link("workouts"),
            )


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def dispatch_weekly_summaries() -> None:
    """
    Hourly, matching both the weekday and the hour.

    Same static-schedule shape as the daily summaries: one beat entry
    regardless of when individual users choose, because Beat wakes hourly
    anyway and a per-user schedule would have to be rebuilt on every change.
    """
    if not settings.is_claude_configured:
        logger.info("Skipping weekly summaries - ANTHROPIC_API_KEY is not configured")
        return

    now = clock.now_local()
    weekday = week_index(now)

    with Session(engine) as session:
        user_ids = session.exec(
            select(User.id)
            .join(GarminAccount, GarminAccount.user_id == User.id)
            .where(
                User.weekly_summary_day == weekday,
                User.weekly_summary_hour == now.hour,
                User.telegram_chat_id.is_not(None),
                GarminAccount.disconnected_at.is_(None),
            )
        ).all()

    for index, user_id in enumerate(user_ids):
        send_weekly_summary_task.apply_async(args=[user_id], countdown=index * 5)


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
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

    current_hour = clock.now_local().hour

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


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    soft_time_limit=SUMMARY_SOFT_LIMIT,
    time_limit=SUMMARY_HARD_LIMIT,
)
def deliver_coach_event_task(self, event_id: int) -> None:
    """
    Sends one coach event. Every rule about whether it should be sent lives
    in services/coach_events.deliver; this only adds the retry. A failure
    leaves the event pending, so the retry picks it up where it stopped.
    """
    with Session(engine) as session:
        try:
            outcome = deliver_coach_event(session, event_id)
        except (ClaudeNotConfiguredError, ClaudeAuthError, ClaudeOutOfCreditError) as exc:
            logger.warning("Coach event %s not delivered - %s", event_id, type(exc).__name__)
            return
        except Exception as exc:
            logger.exception("Coach event %s failed", event_id)
            raise self.retry(exc=exc)
    logger.info("Coach event %s: %s", event_id, outcome)


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def backfill_notifications_task(user_id: int) -> None:
    """
    Queued by the Mini App (services/notifications.request_backfill), which
    cannot read chat_message itself. Idempotent, so a second run is harmless.
    """
    with Session(engine) as session:
        added = notifications.backfill_from_coach_events(session, user_id)
    if added:
        logger.info("Notifications backfill for user %s: %s added", user_id, added)


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def poll_watch_uploads() -> None:
    """
    Every 30 minutes through the day: has anyone's watch uploaded something?

    Without this the post-workout message waited for the next scheduled
    sync - a 09:00 run answered at 20:00. Only users who want that message
    are checked, and each check is one small request; a full sync follows
    only when there really is something new.
    """
    if not settings.watch_poll_enabled or garmin_is_throttled():
        return

    with Session(engine) as session:
        user_ids = session.exec(
            select(User.id)
            .join(GarminAccount, GarminAccount.user_id == User.id)
            .where(
                GarminAccount.disconnected_at.is_(None),
                User.telegram_chat_id.is_not(None),
                User.notify_post_workout.is_(True),
            )
        ).all()

    # Spread out, for the same single-IP reason as the scheduled sync.
    for index, user_id in enumerate(user_ids):
        check_watch_upload_task.apply_async(args=[user_id], countdown=index * 3)


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def check_watch_upload_task(user_id: int) -> None:
    """
    One user's cheap check. It does not store the new upload time itself:
    the sync it triggers does, and the sync relies on seeing the old value
    first to tell "new data" from "nothing new".
    """
    if garmin_is_throttled():
        return

    with Session(engine) as session:
        account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user_id)).first()
        if account is None or account.disconnected_at is not None:
            return
        stored = account.watch_last_upload_at
        token = account.encrypted_token

    try:
        latest = get_watch_last_upload(resume_garmin_session(decrypt(token)))
    except GarminRateLimitError:
        mark_garmin_throttled()
        return
    except GarminAuthError:
        return  # the next scheduled sync handles a dead token and tells the user

    if latest is None:
        return
    if stored is not None and stored.tzinfo is None:
        stored = stored.replace(tzinfo=timezone.utc)
    if stored is None or latest > stored:
        sync_one_user_task.delay(user_id)


# --------------------------------------------------------------------------
# Workouts on the watch. On the default queue: each is two or three small
# Garmin requests, like a sync, and nothing here calls Claude.
# --------------------------------------------------------------------------

WORKOUT_REMOVE_KEYBOARD_TEXT = "🗑 הסרה מהשעון"


@contextmanager
def _workout_errors(chat_id: int, action: str):
    """Every way a Garmin workout action can fail, turned into one clear message."""
    try:
        yield
    except GarminRateLimitError:
        mark_garmin_throttled()
        send_telegram_message(chat_id, "⏳ גרמין מגבילים כרגע את הבקשות. נסה שוב בעוד כמה דקות.")
    except GarminAuthError:
        send_telegram_message(chat_id, "🔌 החיבור לגרמין פג תוקף. שלח /start כדי להתחבר מחדש.")
    except GarminRequestError as exc:
        # What was sent is in workout.sent_json; this line says why it failed.
        logger.error("Garmin rejected workout %s: %s", action, exc)
        send_telegram_message(
            chat_id, "⚠️ Garmin לא קיבלו את האימון. רשמתי את זה. אפשר לבקש מהמאמן גרסה פשוטה יותר."
        )
    except (workout_service.WorkoutNotFound, PermissionError):
        send_telegram_message(chat_id, "האימון הזה לא זמין לשליחה.")


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def send_workout_task(user_id: int, workout_id: int, day_iso: str) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None or user.telegram_chat_id is None:
            return
        with _workout_errors(user.telegram_chat_id, f"send {workout_id}"):
            row = workout_service.send_to_watch(
                session, user, workout_id, datetime.strptime(day_iso, "%Y-%m-%d").date()
            )
            send_telegram_message(
                user.telegram_chat_id,
                f"✅ *{row.name}* נכנס ללוח של Garmin ל-{row.scheduled_for.strftime('%d.%m')}.\n"
                "הוא יופיע בשעון אחרי הסנכרון הבא שלו.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(WORKOUT_REMOVE_KEYBOARD_TEXT, callback_data=f"wk:drop:{row.id}")
                ]]),
            )


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def workout_fit_task(user_id: int, workout_id: int) -> None:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None or user.telegram_chat_id is None:
            return
        with _workout_errors(user.telegram_chat_id, f"fit {workout_id}"):
            name, content = workout_service.fit_file(session, user, workout_id)
            send_telegram_document(user.telegram_chat_id, content, name, caption="📄 קובץ .fit")


@celery_app.task(soft_time_limit=SMALL_SOFT_LIMIT, time_limit=SMALL_HARD_LIMIT)
def remove_workout_task(user_id: int, workout_id: int, replaced: bool = False) -> None:
    """`replaced`: the old version of a workout that was just edited - said differently."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None or user.telegram_chat_id is None:
            return
        with _workout_errors(user.telegram_chat_id, f"remove {workout_id}"):
            workout_service.remove_from_watch(session, user, workout_id)
            send_telegram_message(
                user.telegram_chat_id,
                "🔁 הגרסה הקודמת הוסרה מהשעון." if replaced else "🗑 נמחק מהלו״ז ומהלוח של Garmin.",
            )
