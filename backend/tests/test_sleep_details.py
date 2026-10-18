"""
Deep sleep fields: parsed on every sync in Python, and backfilled for the
history by plain SQL in the migration. Both must give the same answer for
the same Garmin response - that is the main thing checked here.
"""

import importlib.util
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import select

from app.models.daily_metric import DailyMetric
from app.services.garmin_sync import _apply_daily_metric
from app.services.metrics_view import format_sleep
from app.services.sleep_details import apply_sleep_details, extract_sleep_details

# Shaped like garminconnect's get_sleep_data() response, trimmed to the
# fields read. 1792364400000 ms = 2026-10-18 23:00 as watch wall-clock time.
SLEEP = {
    "dailySleepDTO": {
        "sleepTimeSeconds": 25200,
        "sleepStartTimestampLocal": 1792364400000,
        "sleepEndTimestampLocal": 1792389600000,
        "averageSpO2Value": 95.4,
        "lowestSpO2Value": 89.0,
        "lowestRespirationValue": 11.2,
        "sleepScores": {"overall": {"value": 83.6, "qualifierKey": "GOOD"}},
    },
    "restlessMomentsCount": 31,
    "bodyBatteryChange": 54,
}

EXPECTED = {
    "sleep_score": 84,
    "sleep_score_qualifier": "GOOD",
    "avg_spo2": 95.4,
    "lowest_spo2": 89.0,
    "lowest_sleep_respiration": 11.2,
    "restless_moments": 31,
    "sleep_body_battery_change": 54,
    "sleep_start_local": datetime(2026, 10, 18, 23, 0),
    "sleep_end_local": datetime(2026, 10, 19, 6, 0),
}

MIGRATION = next((Path(__file__).parent.parent / "alembic" / "versions").glob("b0717384dcf6_*.py"))


def _backfill_sql() -> str:
    spec = importlib.util.spec_from_file_location("migration_b0717384dcf6", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BACKFILL_SQL


def _columns(metric) -> dict:
    return {name: getattr(metric, name) for name in EXPECTED}


def test_python_extracts_every_field():
    assert extract_sleep_details(SLEEP) == EXPECTED


def test_missing_pulse_ox_stays_empty():
    without = {"dailySleepDTO": {k: v for k, v in SLEEP["dailySleepDTO"].items() if "SpO2" not in k}}
    details = extract_sleep_details(without)
    assert details["avg_spo2"] is None and details["lowest_spo2"] is None


def test_resync_without_a_value_keeps_the_stored_one():
    metric = DailyMetric(user_id=1, date=date(2026, 10, 19), sleep_score=80)
    apply_sleep_details(metric, {"dailySleepDTO": {}})
    assert metric.sleep_score == 80


def test_sync_stores_the_fields(session, make_user):
    me = make_user()
    payload = {"summary": {}, "sleep": SLEEP, "hrv": {}, "max_metrics": [], "training": {}}
    _apply_daily_metric(session, me.id, date(2026, 10, 19), payload)
    session.commit()

    stored = session.exec(select(DailyMetric).where(DailyMetric.user_id == me.id)).one()
    assert _columns(stored) == EXPECTED


def test_sql_backfill_matches_the_python_parser(session, make_user):
    me = make_user()
    session.add(DailyMetric(user_id=me.id, date=date(2026, 10, 19), raw_json={"sleep": SLEEP}))
    session.commit()

    session.execute(text(_backfill_sql()))
    session.commit()
    session.expire_all()

    stored = session.exec(select(DailyMetric).where(DailyMetric.user_id == me.id)).one()
    assert _columns(stored) == extract_sleep_details(SLEEP)


def test_backfill_never_overwrites_and_survives_a_malformed_row(session, make_user):
    me = make_user()
    broken = {"dailySleepDTO": {"averageSpO2Value": "n/a", "sleepScores": {"overall": {"value": "?"}}}}
    session.add(DailyMetric(user_id=me.id, date=date(2026, 10, 18), raw_json={"sleep": broken}))
    session.add(DailyMetric(user_id=me.id, date=date(2026, 10, 19), raw_json={"sleep": SLEEP}, sleep_score=70))
    session.commit()

    session.execute(text(_backfill_sql()))  # must not raise
    session.commit()
    session.expire_all()

    rows = {m.date: m for m in session.exec(select(DailyMetric).where(DailyMetric.user_id == me.id)).all()}
    assert rows[date(2026, 10, 18)].avg_spo2 is None
    assert rows[date(2026, 10, 19)].sleep_score == 70  # already set - kept


@pytest.fixture
def last_night(session, make_user):
    me = make_user()
    from app.core import clock

    metric = DailyMetric(user_id=me.id, date=clock.today(), sleep_duration_minutes=420)
    apply_sleep_details(metric, SLEEP)
    session.add(metric)
    session.commit()
    return me


def test_sleep_screen_shows_the_new_details(session, last_night):
    screen = format_sleep(session, last_night.id)
    assert "84" in screen and "95%" in screen and "23:00" in screen


def test_sleep_screen_hides_what_was_not_measured(session, make_user):
    from app.core import clock

    me = make_user()
    session.add(DailyMetric(user_id=me.id, date=clock.today(), sleep_duration_minutes=420))
    session.commit()

    assert "SpO2" not in format_sleep(session, me.id)
