"""
The deeper sleep fields, read out of Garmin's sleep response.

Two paths fill them, and they must agree:
  - every sync, through apply_sleep_details() below
  - nights stored before these columns existed, through the SQL backfill
    in the migration that added them (raw_json already held everything)
tests/test_sleep_details.py runs both on the same response and compares.

Field names follow garminconnect's get_sleep_data() response. Anything
missing - an older watch, Pulse Ox switched off, Garmin renaming a key -
yields None, never a substitute value.
"""

from datetime import datetime, timezone
from typing import Optional

from app.models.daily_metric import DailyMetric


def _local_from_millis(value) -> Optional[datetime]:
    # Garmin's *TimestampLocal fields are the watch's wall-clock time written
    # as if it were UTC epoch milliseconds - so reading them as UTC gives the
    # local time back. A naive datetime, the same shape as Activity.start_time.
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).replace(tzinfo=None)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _whole(value):
    # Rounded, not truncated - the migration's SQL cast rounds, and the two
    # paths must store the same number for the same night.
    number = _number(value)
    return int(round(number)) if number is not None else None


def extract_sleep_details(sleep: Optional[dict]) -> dict:
    sleep = sleep or {}
    dto = sleep.get("dailySleepDTO") or {}
    overall = (dto.get("sleepScores") or {}).get("overall") or {}

    return {
        "sleep_score": _whole(overall.get("value")),
        "sleep_score_qualifier": overall.get("qualifierKey") if isinstance(overall.get("qualifierKey"), str) else None,
        "avg_spo2": _number(dto.get("averageSpO2Value")),
        "lowest_spo2": _number(dto.get("lowestSpO2Value")),
        "lowest_sleep_respiration": _number(dto.get("lowestRespirationValue")),
        "restless_moments": _whole(sleep.get("restlessMomentsCount")),
        "sleep_body_battery_change": _whole(sleep.get("bodyBatteryChange")),
        "sleep_start_local": _local_from_millis(dto.get("sleepStartTimestampLocal")),
        "sleep_end_local": _local_from_millis(dto.get("sleepEndTimestampLocal")),
    }


def apply_sleep_details(metric: DailyMetric, sleep: Optional[dict]) -> None:
    """
    Only overwrite when Garmin returned a value - the same rule the rest of
    the daily sync follows, so a re-sync of a day Garmin has trimmed cannot
    erase a score that was already stored.
    """
    for name, value in extract_sleep_details(sleep).items():
        if value is not None:
            setattr(metric, name, value)
