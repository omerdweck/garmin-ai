"""
The anomaly rules, as pure functions: numbers in, a verdict out. No
database, no clock, no settings lookup inside - so each rule is tested with
a handful of literal values, and what makes it fire is readable in one place.

Every rule answers one of three things:
    FIRING  - the pattern is there
    CLEAR   - there is enough data, and the pattern is not there
    UNKNOWN - not enough data to say (watch not worn, too little history)

UNKNOWN is not CLEAR on purpose: a night without the watch must not "end"
an episode of poor sleep and let it be announced again the next day.

Each verdict that depends on a baseline is measured against the athlete's
*own* previous weeks - never against a population average. A resting HR of
58 is excellent for one person and a warning sign for another.
"""

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from app.core.config import settings

FIRING = "firing"
CLEAR = "clear"
UNKNOWN = "unknown"

RULE_HRV = "hrv_drop"
RULE_RHR = "rhr_high"
RULE_SLEEP = "sleep_poor"
RULE_BODY_BATTERY = "body_battery_low"
RULE_EASY_RUN = "easy_run_drift"
RULE_LOAD = "load_spike"
RULES = [RULE_HRV, RULE_RHR, RULE_SLEEP, RULE_BODY_BATTERY, RULE_EASY_RUN, RULE_LOAD]


@dataclass(frozen=True)
class Thresholds:
    min_baseline_days: int
    hrv_std_factor: float
    rhr_delta: int
    sleep_min_minutes: int
    sleep_min_score: int
    body_battery_recovery: int
    easy_run_drop_pct: float
    acwr_max: float
    volume_jump_pct: float
    min_weekly_minutes: int
    min_chronic_load: int

    @classmethod
    def from_settings(cls) -> "Thresholds":
        return cls(
            min_baseline_days=settings.anomaly_min_baseline_days,
            hrv_std_factor=settings.anomaly_hrv_std_factor,
            rhr_delta=settings.anomaly_rhr_delta,
            sleep_min_minutes=settings.anomaly_sleep_min_minutes,
            sleep_min_score=settings.anomaly_sleep_min_score,
            body_battery_recovery=settings.anomaly_body_battery_recovery,
            easy_run_drop_pct=settings.anomaly_easy_run_drop_pct,
            acwr_max=settings.anomaly_acwr_max,
            volume_jump_pct=settings.anomaly_volume_jump_pct,
            min_weekly_minutes=settings.anomaly_min_weekly_minutes,
            min_chronic_load=settings.anomaly_min_chronic_load,
        )


@dataclass(frozen=True)
class Baseline:
    mean: float
    std: float
    samples: int


@dataclass(frozen=True)
class Finding:
    status: str
    details: dict = field(default_factory=dict)


def make_baseline(values: list, min_samples: int) -> Optional[Baseline]:
    known = [float(v) for v in values if v is not None]
    if len(known) < min_samples:
        return None
    return Baseline(mean=statistics.fmean(known), std=statistics.pstdev(known), samples=len(known))


def _days(series: dict, end: date, count: int) -> list:
    """The values of the last `count` days ending on `end`, oldest first."""
    return [series.get(end - timedelta(days=offset)) for offset in range(count - 1, -1, -1)]


def hrv_drop(series: dict, end: date, base: Optional[Baseline], t: Thresholds) -> Finding:
    """Three falls in a row, and today clearly below the athlete's usual."""
    values = _days(series, end, 4)
    if base is None or any(v is None for v in values):
        return Finding(UNKNOWN)
    falling = all(later < earlier for earlier, later in zip(values, values[1:]))
    low = values[-1] < base.mean - t.hrv_std_factor * base.std
    if not (falling and low):
        return Finding(CLEAR)
    return Finding(FIRING, {"values": values[1:], "baseline": round(base.mean)})


def rhr_high(series: dict, end: date, base: Optional[Baseline], t: Thresholds) -> Finding:
    """Two of the last three days at least `rhr_delta` beats above the usual."""
    known = [v for v in _days(series, end, 3) if v is not None]
    if base is None or len(known) < 2:
        return Finding(UNKNOWN)
    high = [v for v in known if v >= base.mean + t.rhr_delta]
    if len(high) < 2:
        return Finding(CLEAR)
    return Finding(FIRING, {"values": known, "baseline": round(base.mean)})


def sleep_poor(durations: dict, scores: dict, end: date, t: Thresholds) -> Finding:
    """
    Two of the last three nights short or poorly scored. Absolute limits
    rather than a baseline: six hours is short whatever someone's habit is.
    """
    nights, bad = [], 0
    for minutes, score in zip(_days(durations, end, 3), _days(scores, end, 3)):
        if minutes is None and score is None:
            continue
        poor = (minutes is not None and minutes < t.sleep_min_minutes) or (
            score is not None and score < t.sleep_min_score
        )
        bad += poor
        nights.append({"minutes": minutes, "score": score, "poor": poor})
    if len(nights) < 2:
        return Finding(UNKNOWN)
    if bad < 2:
        return Finding(CLEAR)
    return Finding(FIRING, {"nights": nights, "poor": bad})


def body_battery_low(series: dict, end: date, t: Thresholds) -> Finding:
    """Three days in a row without recharging above the threshold."""
    values = _days(series, end, 3)
    if any(v is None for v in values):
        return Finding(UNKNOWN)
    if not all(v < t.body_battery_recovery for v in values):
        return Finding(CLEAR)
    return Finding(FIRING, {"values": values, "threshold": t.body_battery_recovery})


def easy_run_drift(recent: list, base: Optional[Baseline], t: Thresholds) -> Finding:
    """
    Two of the last three easy runs needing clearly more heartbeats per
    metre than usual - the heart working harder for the same easy effort.

    `recent` is the efficiency (metres per heartbeat) of the latest easy
    runs, oldest first. Per heartbeat rather than "HR above zone 2": we do
    not store anyone's zones, and a ratio against their own runs needs none.
    """
    if base is None or len(recent) < 3:
        return Finding(UNKNOWN)
    last_three = recent[-3:]
    floor = base.mean * (1 - t.easy_run_drop_pct / 100)
    worse = [v for v in last_three if v < floor and v < base.mean - base.std]
    if len(worse) < 2:
        return Finding(CLEAR)
    average = statistics.fmean(last_three)
    return Finding(FIRING, {"drop_pct": round((1 - average / base.mean) * 100, 1)})


def load_spike(
    acute: Optional[float],
    chronic: Optional[float],
    last_week_minutes: Optional[float],
    previous_weekly_minutes: Optional[float],
    t: Thresholds,
) -> Finding:
    """
    Either Garmin's acute:chronic ratio is high, or this week's training
    time jumped well past the four weeks before it.
    """
    known = False
    details: dict = {}

    if acute is not None and chronic is not None and chronic >= t.min_chronic_load:
        known = True
        ratio = acute / chronic
        if ratio > t.acwr_max:
            details["acwr"] = round(ratio, 2)

    if last_week_minutes is not None and previous_weekly_minutes is not None and (
        previous_weekly_minutes >= t.min_weekly_minutes
    ):
        known = True
        jump = (last_week_minutes / previous_weekly_minutes - 1) * 100
        if jump > t.volume_jump_pct and last_week_minutes - previous_weekly_minutes >= 60:
            details.update(
                last_week_minutes=round(last_week_minutes),
                previous_weekly_minutes=round(previous_weekly_minutes),
                jump_pct=round(jump),
            )

    if not known:
        return Finding(UNKNOWN)
    return Finding(FIRING, details) if details else Finding(CLEAR)
