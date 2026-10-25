"""
The one-time import of a user's past year, a little every night.

Why a separate path from the routine sync and the 30-day backfill on link:
those must finish in minutes and run whenever needed. A year is ~1,600
Garmin requests per person - fine as a trickle at night, and exactly the
burst that gets the server's IP blocked if done at once.

How a run works (run_chunk):
  1. Activities first, once: Garmin lists them newest first in pages, so a
     handful of requests covers the whole year.
  2. Then days, newest to oldest. A day that already has a row (from the
     routine syncs) is skipped without asking Garmin. Up to `chunk_days`
     days are fetched per run, each committed on its own, so stopping at
     any point - throttling, a deploy, a crash - loses nothing.

What it never does: send messages. Achievements and "new record!" only
ever fire for workouts from the last 36 hours (see post_workout.py), so a
year of old workouts arriving at once is silent by design.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from garminconnect.exceptions import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
from sqlmodel import Session, select

from app.core.garmin_client import GarminAuthError, GarminRateLimitError, get_activities
from app.models.daily_metric import DailyMetric
from app.models.garmin_account import GarminAccount
from app.models.history_import import (
    STATUS_DONE,
    STATUS_PAUSED,
    STATUS_PENDING,
    STATUS_RUNNING,
    HistoryImport,
)
from app.models.user import User
from app.services.garmin_sync import fetch_history_day, store_activities, store_history_day

logger = logging.getLogger(__name__)

ACTIVITY_PAGE_SIZE = 100
# A hard stop, whatever Garmin returns: 30 pages is 3,000 workouts.
MAX_ACTIVITY_PAGES = 30

# The library raises its own exceptions from data calls; the wrapped ones
# come from login. Either kind means the same thing here.
RATE_LIMIT_ERRORS = (GarminRateLimitError, GarminConnectTooManyRequestsError)
AUTH_ERRORS = (GarminAuthError, GarminConnectAuthenticationError)


class ImportThrottled(Exception):
    """Garmin is rate-limiting. Progress so far is saved; try another night."""


def in_night_window(hour: int, start_hour: int, end_hour: int) -> bool:
    """True inside [start, end) on a clock that may wrap midnight (23 -> 6)."""
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def eligible_user_ids(session: Session, scope: str, admin_chat_id: Optional[int]) -> list[int]:
    """Users the import may run for: linked, connected, and inside the scope."""
    if scope == "off":
        return []
    query = (
        select(User.id)
        .join(GarminAccount, GarminAccount.user_id == User.id)
        .where(User.is_active == True, GarminAccount.disconnected_at.is_(None))  # noqa: E712
    )
    if scope != "all":
        if admin_chat_id is None:
            return []
        query = query.where(User.telegram_chat_id == admin_chat_id)
    return list(session.exec(query).all())


def ensure_import(session: Session, user_id: int, today: date, days: int) -> HistoryImport:
    """The user's import row, created the first time with a fixed range."""
    record = session.exec(select(HistoryImport).where(HistoryImport.user_id == user_id)).first()
    if record is not None:
        return record
    newest = today - timedelta(days=1)  # today is the routine sync's job
    record = HistoryImport(
        user_id=user_id,
        newest_date=newest,
        oldest_date=newest - timedelta(days=days - 1),
        next_date=newest,
        days_total=days,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def next_user_to_import(session: Session, user_ids: list[int]) -> Optional[int]:
    """
    The unfinished import touched longest ago - so several users take turns
    night after night instead of the first one hogging every run.
    """
    if not user_ids:
        return None
    return session.exec(
        select(HistoryImport.user_id)
        .where(
            HistoryImport.user_id.in_(user_ids),
            HistoryImport.status.in_([STATUS_PENDING, STATUS_RUNNING]),
        )
        .order_by(HistoryImport.updated_at)
    ).first()


def _touch(session: Session, record: HistoryImport) -> None:
    record.updated_at = datetime.now(timezone.utc)
    session.add(record)
    session.commit()


def _import_activities(
    session: Session,
    record: HistoryImport,
    garmin_session,
    pause_seconds: float,
    list_activities: Callable,
) -> None:
    for page in range(MAX_ACTIVITY_PAGES):
        batch = list_activities(garmin_session, page * ACTIVITY_PAGE_SIZE, ACTIVITY_PAGE_SIZE) or []
        in_range = [
            a for a in batch
            if a.get("startTimeLocal") and datetime.fromisoformat(a["startTimeLocal"]).date() >= record.oldest_date
        ]
        record.activities_imported += store_activities(session, record.user_id, in_range)
        _touch(session, record)
        # Newest first: once a page reaches past the range, or comes back
        # short, there is nothing older worth asking for.
        if len(batch) < ACTIVITY_PAGE_SIZE or len(in_range) < len(batch):
            break
        if pause_seconds:
            time.sleep(pause_seconds)
    record.activities_done = True
    _touch(session, record)


def _day_is_stored(session: Session, user_id: int, day: date) -> bool:
    return session.exec(
        select(DailyMetric.id).where(
            DailyMetric.user_id == user_id,
            DailyMetric.date == day,
            DailyMetric.raw_json.is_not(None),
        )
    ).first() is not None


def run_chunk(
    session: Session,
    record: HistoryImport,
    garmin_session,
    chunk_days: int,
    pause_seconds: float,
    list_activities: Callable = get_activities,
    fetch_day: Callable = fetch_history_day,
) -> int:
    """
    One night-run's worth of work. Returns how many days were fetched from
    Garmin. Raises ImportThrottled if Garmin starts refusing; marks the
    import paused if Garmin rejects the login.
    """
    if record.status == STATUS_DONE:
        return 0
    if record.started_at is None:
        record.started_at = datetime.now(timezone.utc)
    record.status = STATUS_RUNNING
    _touch(session, record)

    fetched = 0
    try:
        if not record.activities_done:
            _import_activities(session, record, garmin_session, pause_seconds, list_activities)

        while record.next_date >= record.oldest_date and fetched < chunk_days:
            day = record.next_date
            if not _day_is_stored(session, record.user_id, day):
                payload = fetch_day(garmin_session, day.isoformat(), pause_seconds)
                store_history_day(session, record.user_id, day, payload)
                fetched += 1
                record.days_fetched += 1
            record.next_date = day - timedelta(days=1)
            record.days_done += 1
            record.last_error = None
            _touch(session, record)
    except RATE_LIMIT_ERRORS as exc:
        session.rollback()
        record.last_error = f"throttled: {exc}"[:500]
        _touch(session, record)
        raise ImportThrottled(str(exc)) from exc
    except AUTH_ERRORS as exc:
        # Not ours to fix: the routine sync notices the same rejected login,
        # tells the user and drops the link. A relink later resumes here.
        session.rollback()
        record.status = STATUS_PAUSED
        record.last_error = f"auth: {exc}"[:500]
        _touch(session, record)
        return fetched

    if record.next_date < record.oldest_date:
        record.status = STATUS_DONE
        record.finished_at = datetime.now(timezone.utc)
        _touch(session, record)
        logger.info(
            "History import done for user %s: %s days fetched, %s activities",
            record.user_id, record.days_fetched, record.activities_imported,
        )
    return fetched


def resume_paused(session: Session, user_ids: list[int]) -> None:
    """A paused import whose user is linked again (in user_ids) goes back in line."""
    paused = session.exec(
        select(HistoryImport).where(HistoryImport.user_id.in_(user_ids), HistoryImport.status == STATUS_PAUSED)
    ).all()
    for record in paused:
        record.status = STATUS_RUNNING
        _touch(session, record)
