"""
The actual "pull data from Garmin and save it" logic, kept independent
of *how* it gets triggered - a manual endpoint calls the same function
a scheduled Celery task will call later, so nothing here needs to
change when the scheduling layer is added.
"""

import time
from datetime import date as date_type
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.crypto import decrypt
from app.core.garmin_client import (
    GarminAuthError,
    GarminRateLimitError,
    Garmin,
    get_activities,
    get_daily_summary,
    get_exercise_sets,
    get_hrv_data,
    get_max_metrics,
    get_sleep_data,
    get_watch_last_upload,
    resume_garmin_session,
)
from app.models.activity import Activity
from app.models.daily_metric import DailyMetric
from app.models.garmin_account import GarminAccount


def _seconds_to_minutes(seconds) -> int | None:
    return seconds // 60 if seconds is not None else None


def _fetch_daily_payload(garmin_session: Garmin, date_str: str) -> dict:
    """
    Every Garmin call for one day, and nothing else. Split out from the
    database write so a write that has to be retried (see _upsert_daily_metric)
    can reuse what was already fetched instead of spending four more requests
    against an API that rate-limits by IP.
    """
    return {
        "summary": get_daily_summary(garmin_session, date_str),
        "sleep": get_sleep_data(garmin_session, date_str),
        "hrv": get_hrv_data(garmin_session, date_str),
        "max_metrics": get_max_metrics(garmin_session, date_str),
    }


def _apply_daily_metric(session: Session, user_id: int, target_date: date_type, payload: dict) -> None:
    summary = payload["summary"]
    sleep = payload["sleep"]
    hrv = payload["hrv"]
    max_metrics = payload["max_metrics"]

    sleep_dto = sleep.get("dailySleepDTO") or {}
    hrv_summary = hrv.get("hrvSummary") or {}
    # max_metrics is a list that's empty on most days (Garmin only
    # recalculates VO2 max after a qualifying activity).
    generic_max = (max_metrics[0].get("generic") or {}) if max_metrics else {}

    existing = session.exec(
        select(DailyMetric).where(DailyMetric.user_id == user_id, DailyMetric.date == target_date)
    ).first()
    metric = existing or DailyMetric(user_id=user_id, date=target_date)

    metric.resting_heart_rate = summary.get("restingHeartRate")
    metric.min_heart_rate = summary.get("minHeartRate")
    metric.max_heart_rate = summary.get("maxHeartRate")
    metric.steps = summary.get("totalSteps")
    metric.total_calories = summary.get("totalKilocalories")
    metric.active_calories = summary.get("activeKilocalories")
    metric.distance_meters = summary.get("totalDistanceMeters")
    metric.floors_climbed = summary.get("floorsAscended")
    metric.avg_stress_level = summary.get("averageStressLevel")
    metric.max_stress_level = summary.get("maxStressLevel")
    metric.body_battery_high = summary.get("bodyBatteryHighestValue")
    metric.body_battery_low = summary.get("bodyBatteryLowestValue")
    metric.moderate_intensity_minutes = summary.get("moderateIntensityMinutes")
    metric.vigorous_intensity_minutes = summary.get("vigorousIntensityMinutes")

    metric.sleep_duration_minutes = _seconds_to_minutes(sleep_dto.get("sleepTimeSeconds"))
    metric.deep_sleep_minutes = _seconds_to_minutes(sleep_dto.get("deepSleepSeconds"))
    metric.light_sleep_minutes = _seconds_to_minutes(sleep_dto.get("lightSleepSeconds"))
    metric.rem_sleep_minutes = _seconds_to_minutes(sleep_dto.get("remSleepSeconds"))
    metric.awake_minutes = _seconds_to_minutes(sleep_dto.get("awakeSleepSeconds"))
    metric.avg_sleep_respiration = sleep_dto.get("averageRespirationValue")
    metric.avg_sleep_stress = sleep_dto.get("avgSleepStress")

    metric.avg_hrv = hrv_summary.get("lastNightAvg")
    metric.hrv_status = hrv_summary.get("status")

    # Only overwrite when Garmin actually returned a value - a later sync of
    # an older day must not blank out a VO2 max we already stored.
    if generic_max.get("vo2MaxPreciseValue") is not None:
        metric.vo2_max = generic_max.get("vo2MaxPreciseValue")
    if generic_max.get("fitnessAge") is not None:
        metric.fitness_age = generic_max.get("fitnessAge")

    metric.raw_json = {"summary": summary, "sleep": sleep, "hrv": hrv, "max_metrics": max_metrics}
    metric.updated_at = datetime.now(timezone.utc)

    session.add(metric)


def _upsert_daily_metric(session: Session, user_id: int, target_date: date_type, payload: dict) -> None:
    """
    Write one day, committing it on its own.

    Per-day commits rather than one transaction for the whole range: a
    30-day backfill that fails on day 25 used to roll back all 24 days that
    had already succeeded, so a single bad day cost the entire month.

    The IntegrityError branch handles two syncs writing the same day at once.
    That is a normal situation, not a corner case - linking an account fires
    a quick 2-day sync *and* a 30-day backfill on purpose, and the scheduled
    run can overlap a manual one. Whoever commits second re-reads the row the
    winner wrote and updates it, reusing the already-fetched payload.
    """
    _apply_daily_metric(session, user_id, target_date, payload)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        _apply_daily_metric(session, user_id, target_date, payload)
        session.commit()


def _sync_activities(session: Session, user_id: int, garmin_session: Garmin, limit: int = 20) -> None:
    for raw_activity in get_activities(garmin_session, 0, limit):
        garmin_activity_id = raw_activity.get("activityId")
        start_time_str = raw_activity.get("startTimeLocal")
        if garmin_activity_id is None or start_time_str is None:
            continue

        # Activities don't change after the fact once recorded - if we've
        # already stored this one, there's nothing to update, just skip it.
        # Scoped to this user: an unscoped check made a second user linking
        # the same Garmin account silently receive zero activities.
        already_synced = session.exec(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.garmin_activity_id == garmin_activity_id,
            )
        ).first()
        if already_synced is not None:
            continue

        activity_type = (raw_activity.get("activityType") or {}).get("typeKey")

        session.add(
            Activity(
                user_id=user_id,
                garmin_activity_id=garmin_activity_id,
                activity_name=raw_activity.get("activityName"),
                activity_type=activity_type,
                start_time=datetime.fromisoformat(start_time_str),
                duration_seconds=raw_activity.get("duration"),
                distance_meters=raw_activity.get("distance"),
                calories=raw_activity.get("calories"),
                avg_heart_rate=raw_activity.get("averageHR"),
                max_heart_rate=raw_activity.get("maxHR"),
                steps=raw_activity.get("steps"),
                raw_json=raw_activity,
            )
        )

        # Commit each activity on its own, for the same reason the daily
        # metrics do: the "already synced?" check above can be overtaken by a
        # concurrent sync between the read and the write. Losing that race is
        # harmless - activities never change after being recorded, so the row
        # the winner wrote is the row we wanted - but it must not abort the
        # rest of the batch, which is what one shared transaction did.
        try:
            session.commit()
        except IntegrityError:
            session.rollback()


def ensure_exercise_sets(session: Session, activity: Activity) -> Activity:
    """
    Lazily fills in the per-set exercise breakdown for a strength workout.

    Not done during sync: it's one extra Garmin request per activity, and
    most workouts are never opened in detail - fetching 30 of them on
    every backfill would spend rate-limit budget on data nobody looks at.
    Cached permanently once fetched, since a completed workout's sets
    can't change. A failure here is non-fatal: the caller still shows
    everything else about the workout.
    """
    if activity.exercise_sets is not None or activity.activity_type != "strength_training":
        return activity

    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == activity.user_id)).first()
    if account is None:
        return activity

    try:
        garmin_session = resume_garmin_session(decrypt(account.encrypted_token))
        activity.exercise_sets = get_exercise_sets(garmin_session, activity.garmin_activity_id)
        session.add(activity)
        session.commit()
        session.refresh(activity)
    except (GarminAuthError, GarminRateLimitError):
        pass

    return activity


def sync_user_garmin_data(
    session: Session,
    user_id: int,
    days_back: int = 2,
    pause_seconds: float = 0.0,
    activity_limit: int = 20,
) -> None:
    """
    Upserts daily_metric for today and the previous `days_back - 1` days
    (re-covering yesterday too by default, since Garmin sometimes
    finalizes a day's data with a delay), plus any new activities.
    Always updates GarminAccount.last_sync_at/last_sync_error - a failed
    sync should be visible, not silent.

    `pause_seconds` spaces out the per-day requests. The routine 2-day sync
    leaves it at 0, but a 30-day backfill is ~120 requests in a burst, which
    is the kind of traffic pattern that gets an IP throttled - a small pause
    trades a slower background job for not being blocked.
    """
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user_id)).first()
    if account is None:
        raise ValueError(f"No Garmin account linked for user {user_id}")

    try:
        garmin_session = resume_garmin_session(decrypt(account.encrypted_token))

        # Read this first, before pulling anything: it is what tells the user
        # whether an empty result means "nothing happened" or "your watch
        # hasn't uploaded since Tuesday". Committed immediately so the value
        # is available to the caller's message even if the pull below fails.
        watch_upload = get_watch_last_upload(garmin_session)
        if watch_upload is not None:
            account.watch_last_upload_at = watch_upload
            session.add(account)
            session.commit()

        for days_ago in range(days_back):
            target_date = date_type.today() - timedelta(days=days_ago)
            payload = _fetch_daily_payload(garmin_session, target_date.isoformat())
            _upsert_daily_metric(session, user_id, target_date, payload)
            if pause_seconds:
                time.sleep(pause_seconds)

        _sync_activities(session, user_id, garmin_session, limit=activity_limit)
    except GarminRateLimitError as exc:
        # Transient - Garmin is throttling us right now, the stored
        # credentials are still fine. Keep the account, just record it.
        account.last_sync_error = str(exc)
        session.add(account)
        session.commit()
        raise
    except GarminAuthError:
        # The stored token is definitively invalid (not throttling - Garmin
        # actually rejected it), so there's nothing a retry would fix.
        # Delete the account rather than leaving a dead link around: this
        # is what makes /start correctly offer to relink instead of saying
        # "already linked" forever, and what a caller (e.g. the Celery task)
        # uses as the signal to notify the user they need to reconnect.
        session.delete(account)
        session.commit()
        raise

    account.last_sync_at = datetime.now(timezone.utc)
    account.last_sync_error = None
    session.add(account)
    session.commit()
