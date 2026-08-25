"""
The actual "pull data from Garmin and save it" logic, kept independent
of *how* it gets triggered - a manual endpoint calls the same function
a scheduled Celery task will call later, so nothing here needs to
change when the scheduling layer is added.
"""

from datetime import date as date_type
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.crypto import decrypt
from app.core.garmin_client import (
    GarminAuthError,
    GarminRateLimitError,
    Garmin,
    get_activities,
    get_daily_summary,
    get_hrv_data,
    get_sleep_data,
    resume_garmin_session,
)
from app.models.activity import Activity
from app.models.daily_metric import DailyMetric
from app.models.garmin_account import GarminAccount


def _seconds_to_minutes(seconds) -> int | None:
    return seconds // 60 if seconds is not None else None


def _sync_daily_metric(session: Session, user_id: int, garmin_session: Garmin, target_date: date_type) -> None:
    date_str = target_date.isoformat()
    summary = get_daily_summary(garmin_session, date_str)
    sleep = get_sleep_data(garmin_session, date_str)
    hrv = get_hrv_data(garmin_session, date_str)

    sleep_dto = sleep.get("dailySleepDTO") or {}
    hrv_summary = hrv.get("hrvSummary") or {}

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

    metric.raw_json = {"summary": summary, "sleep": sleep, "hrv": hrv}
    metric.updated_at = datetime.now(timezone.utc)

    session.add(metric)


def _sync_activities(session: Session, user_id: int, garmin_session: Garmin, limit: int = 20) -> None:
    for raw_activity in get_activities(garmin_session, 0, limit):
        garmin_activity_id = raw_activity.get("activityId")
        start_time_str = raw_activity.get("startTimeLocal")
        if garmin_activity_id is None or start_time_str is None:
            continue

        # Activities don't change after the fact once recorded - if we've
        # already stored this one, there's nothing to update, just skip it.
        already_synced = session.exec(
            select(Activity).where(Activity.garmin_activity_id == garmin_activity_id)
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


def sync_user_garmin_data(session: Session, user_id: int, days_back: int = 2) -> None:
    """
    Upserts daily_metric for today and the previous `days_back - 1` days
    (re-covering yesterday too by default, since Garmin sometimes
    finalizes a day's data with a delay), plus any new activities.
    Always updates GarminAccount.last_sync_at/last_sync_error - a failed
    sync should be visible, not silent.
    """
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user_id)).first()
    if account is None:
        raise ValueError(f"No Garmin account linked for user {user_id}")

    try:
        garmin_session = resume_garmin_session(decrypt(account.encrypted_token))

        for days_ago in range(days_back):
            target_date = date_type.today() - timedelta(days=days_ago)
            _sync_daily_metric(session, user_id, garmin_session, target_date)

        _sync_activities(session, user_id, garmin_session)
    except (GarminAuthError, GarminRateLimitError) as exc:
        account.last_sync_error = str(exc)
        session.add(account)
        session.commit()
        raise

    account.last_sync_at = datetime.now(timezone.utc)
    account.last_sync_error = None
    session.add(account)
    session.commit()
