"""
One row per user per calendar day, combining three separate Garmin API
calls (daily summary, sleep, HRV) into a single record - this is the
table the Telegram bot/AI queries for trend questions ("how was my
sleep this week"). Deliberately NOT storing intraday time-series here
(e.g. HRV readings every 5 minutes) - see the project discussion this
was based on: daily aggregates fit the trend-query use case, and
interactive frontend charts (which do need time-series) will fetch
that live from Garmin instead of through this table.
"""

from datetime import date as date_type
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class DailyMetric(SQLModel, table=True):
    __tablename__ = "daily_metric"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_daily_metric_user_date"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    date: date_type = Field(index=True)

    # from Garmin's daily summary endpoint
    resting_heart_rate: Optional[int] = None
    min_heart_rate: Optional[int] = None
    max_heart_rate: Optional[int] = None
    steps: Optional[int] = None
    total_calories: Optional[int] = None
    active_calories: Optional[int] = None
    distance_meters: Optional[float] = None
    floors_climbed: Optional[float] = None
    avg_stress_level: Optional[int] = None
    max_stress_level: Optional[int] = None
    body_battery_high: Optional[int] = None
    body_battery_low: Optional[int] = None
    moderate_intensity_minutes: Optional[int] = None
    vigorous_intensity_minutes: Optional[int] = None

    # from Garmin's sleep endpoint
    sleep_duration_minutes: Optional[int] = None
    deep_sleep_minutes: Optional[int] = None
    light_sleep_minutes: Optional[int] = None
    rem_sleep_minutes: Optional[int] = None
    awake_minutes: Optional[int] = None
    avg_sleep_respiration: Optional[float] = None
    avg_sleep_stress: Optional[float] = None

    # from Garmin's HRV endpoint - lastNightAvg/status, not the many
    # intraday readings (those are exactly the time-series data we're
    # not storing here)
    avg_hrv: Optional[int] = None
    hrv_status: Optional[str] = None

    # from Garmin's max-metrics endpoint. Sparse by nature: Garmin only
    # recalculates VO2 max on days with a qualifying activity (e.g. a run),
    # so most rows have None here - readers should look back for the most
    # recent non-null value rather than expecting today's row to have one.
    vo2_max: Optional[float] = None
    fitness_age: Optional[int] = None

    # Full raw responses from all three calls, keyed by source
    # ({"summary": ..., "sleep": ..., "hrv": ...}) - lets us backfill new
    # structured columns later without re-fetching from Garmin.
    raw_json: Optional[dict] = Field(default=None, sa_column=Column(JSONB))

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
