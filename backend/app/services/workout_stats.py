"""
The numbers behind the Mini App's workouts tab - computed here in Python,
drawn by the browser. Read-only: nothing in this module writes.

Periods match the page's selector exactly (frontend/src/range.tsx):
    week  - Sunday to Saturday
    month - the calendar month
    year  - the 365 days ending today
`offset` 0 is the current period, 1 the one before.

Training load comes from Garmin's own per-workout figure
(raw_json["activityTrainingLoad"]). It exists for every Garmin workout,
including the year the history import brought in, so Fitness/Fatigue can
be drawn without a single extra request. Manually entered workouts have no
load - they count in hours and distance, not in the load lines.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.activity import Activity
from app.services.achievements import SPORT_FAMILIES

# The tab's sports. Achievements know four families; the dashboard also
# separates walking from everything else, as in the video.
DASHBOARD_FAMILIES = {
    **SPORT_FAMILIES,
    "walking": {"walking", "hiking", "casual_walking", "speed_walking"},
}
SPORT_KEYS = ["running", "cycling", "swimming", "strength", "walking", "other"]

# Fitness is the 42-day average of daily load, Fatigue the 7-day one -
# the classic CTL/ATL time constants, and what the plan describes.
FITNESS_DAYS = 42
FATIGUE_DAYS = 7


def dashboard_sport(activity_type: Optional[str]) -> str:
    for family, types in DASHBOARD_FAMILIES.items():
        if activity_type in types:
            return family
    return "other"


def period_for(kind: str, offset: int, today: date) -> tuple[date, date]:
    if kind == "week":
        # Python's weekday() is Monday=0; the week here starts on Sunday.
        sunday = today - timedelta(days=(today.weekday() + 1) % 7 + 7 * offset)
        return sunday, sunday + timedelta(days=6)
    if kind == "month":
        year, month = today.year, today.month - offset
        while month <= 0:
            month += 12
            year -= 1
        start = date(year, month, 1)
        next_month = date(year + (month == 12), month % 12 + 1, 1)
        return start, next_month - timedelta(days=1)
    if kind == "year":
        end = today - timedelta(days=365 * offset)
        return end - timedelta(days=364), end
    raise ValueError(f"unknown period kind: {kind}")


@dataclass
class Row:
    day: date
    sport: str
    seconds: float
    moving_seconds: float
    distance_m: float
    elevation_m: float
    load: float


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _rows(session: Session, user_id: int, start: date, end: date) -> list[Row]:
    """Every workout between start and end, inclusive, as plain numbers."""
    activities = session.exec(
        select(Activity.start_time, Activity.activity_type, Activity.duration_seconds,
               Activity.distance_meters, Activity.raw_json)
        .where(
            Activity.user_id == user_id,
            Activity.start_time >= start,
            Activity.start_time < end + timedelta(days=1),
        )
    ).all()
    rows = []
    for start_time, activity_type, duration, distance, raw in activities:
        raw = raw or {}
        seconds = _num(duration)
        rows.append(Row(
            day=start_time.date(),
            sport=dashboard_sport(activity_type),
            seconds=seconds,
            # Moving time leaves out the stops; fall back to elapsed time
            # for workouts that don't record it (strength, manual entries).
            moving_seconds=_num(raw.get("movingDuration")) or seconds,
            distance_m=_num(distance),
            elevation_m=_num(raw.get("elevationGain")),
            load=_num(raw.get("activityTrainingLoad")),
        ))
    return rows


def _totals(rows: list[Row]) -> dict:
    return {
        "workouts": len(rows),
        "distance_km": round(sum(r.distance_m for r in rows) / 1000, 1),
        "moving_hours": round(sum(r.moving_seconds for r in rows) / 3600, 1),
        "elevation_m": round(sum(r.elevation_m for r in rows)),
    }


def _bucket_start(day: date, kind: str) -> date:
    # A year of daily columns is unreadable on a phone: weekly there.
    if kind == "year":
        return day - timedelta(days=(day.weekday() + 1) % 7)
    return day


def summary(session: Session, user_id: int, kind: str, offset: int, today: date) -> dict:
    """The four big numbers, the same for the period before, and hours by sport."""
    start, end = period_for(kind, offset, today)
    prev_start, prev_end = period_for(kind, offset + 1, today)
    rows = _rows(session, user_id, start, end)

    hours: dict[date, dict[str, float]] = {}
    cursor = _bucket_start(start, kind)
    step = timedelta(days=7 if kind == "year" else 1)
    while cursor <= end:
        hours[cursor] = {sport: 0.0 for sport in SPORT_KEYS}
        cursor += step
    for row in rows:
        hours[_bucket_start(row.day, kind)][row.sport] += row.seconds / 3600

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "totals": _totals(rows),
        "previous": _totals(_rows(session, user_id, prev_start, prev_end)),
        "bucket": "week" if kind == "year" else "day",
        "hours_by_sport": [
            {"start": day.isoformat(), **{k: round(v, 2) for k, v in by_sport.items()}}
            for day, by_sport in sorted(hours.items())
        ],
    }


def _daily_load(session: Session, user_id: int, end: date) -> tuple[Optional[date], dict[date, float]]:
    first = session.exec(
        select(Activity.start_time).where(Activity.user_id == user_id).order_by(Activity.start_time)
    ).first()
    if first is None:
        return None, {}
    load: dict[date, float] = defaultdict(float)
    for row in _rows(session, user_id, first.date(), end):
        load[row.day] += row.load
    return first.date(), load


def fitness_fatigue(daily_load: list[float]) -> list[tuple[float, float, float]]:
    """(Fitness, Fatigue, Form) for each day; a day without workouts is load 0."""
    fitness = fatigue = 0.0
    out = []
    for load in daily_load:
        fitness += (load - fitness) / FITNESS_DAYS
        fatigue += (load - fatigue) / FATIGUE_DAYS
        out.append((fitness, fatigue, fitness - fatigue))
    return out


def load_series(session: Session, user_id: int, kind: str, offset: int, today: date) -> dict:
    """
    Fitness, Fatigue and Form for each day of the period. The averages are
    run from the user's very first workout, not from the period's start -
    otherwise every period would begin at zero and climb for no reason.
    """
    start, end = period_for(kind, offset, today)
    end = min(end, today)  # the future has no load
    first, load = _daily_load(session, user_id, end)
    days = []
    if first is not None and first <= end:
        all_days = [first + timedelta(days=i) for i in range((end - first).days + 1)]
        for day, (fitness, fatigue, form) in zip(all_days, fitness_fatigue([load.get(d, 0.0) for d in all_days])):
            if day >= start:
                days.append({
                    "date": day.isoformat(),
                    "load": round(load.get(day, 0.0)),
                    "fitness": round(fitness, 1),
                    "fatigue": round(fatigue, 1),
                    "form": round(form, 1),
                })
    return {"start": start.isoformat(), "end": end.isoformat(), "days": days}


def calendar(session: Session, user_id: int, today: date) -> dict:
    """Load on every day of the last 365, for the year heatmap."""
    start = today - timedelta(days=364)
    load: dict[date, float] = defaultdict(float)
    for row in _rows(session, user_id, start, today):
        load[row.day] += row.load or row.seconds / 60  # no load figure: minutes stand in
    return {
        "start": start.isoformat(),
        "end": today.isoformat(),
        "days": [{"date": d.isoformat(), "load": round(v)} for d, v in sorted(load.items())],
    }
