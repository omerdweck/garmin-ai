"""
Anomaly detection for one athlete: load their own history, compute what is
normal for them, run the rules (anomaly_rules.py), and keep track of
episodes so each one is announced once.

Runs inside the sync, under the per-user sync lock, so two evaluations of
the same user never overlap. It reads only what is already stored - no
Garmin requests.

The message itself is written in anomaly_message.py; this module does not
import the AI client, so the coach's tools can read from it freely.
"""

import logging
import statistics
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.core import clock
from app.models.activity import Activity
from app.models.coach_event import KIND_ANOMALY, CoachEvent
from app.models.daily_metric import DailyMetric
from app.models.metric_alert import MetricAlert, MetricBaseline
from app.services import anomaly_rules as rules
from app.services.coach_events import record_event

logger = logging.getLogger(__name__)

# The baseline is the 28 days *before* the days being judged, so a bad
# stretch does not drag "normal" toward itself while it is being measured.
BASELINE_DAYS = 28
JUDGED_DAYS = 4  # HRV looks at four values (three falls); the rest at three

# Runs counted as easy, for the efficiency rule.
EASY_RUN_TYPES = {"running", "treadmill_running"}
EASY_RUN_MIN_SECONDS = 20 * 60
EASY_RUN_LOOKBACK_DAYS = 60
EASY_RUN_RECENT_DAYS = 14
EASY_RUN_MIN_BASELINE_RUNS = 4

# A rule that comes back within this many days of ending continues the
# same episode instead of starting a new one - so a metric that hovers at
# the threshold is announced once, not every time it crosses back.
REOPEN_WITHIN_DAYS = 3
# An open episode with no data for this long (watch in a drawer) is closed.
STALE_AFTER_DAYS = 7

DAILY_METRICS = {
    "resting_heart_rate": "resting_heart_rate",
    "avg_hrv": "avg_hrv",
    "sleep_duration_minutes": "sleep_duration_minutes",
    "sleep_score": "sleep_score",
    "body_battery_high": "body_battery_high",
}
METRIC_EASY_RUN = "easy_run_efficiency"


# --- loading ---------------------------------------------------------------

def _daily_series(session: Session, user_id: int, start: date, end: date) -> dict[str, dict]:
    rows = session.exec(
        select(DailyMetric).where(
            DailyMetric.user_id == user_id, DailyMetric.date >= start, DailyMetric.date <= end
        )
    ).all()
    series: dict[str, dict] = {name: {} for name in DAILY_METRICS}
    for row in rows:
        for name, column in DAILY_METRICS.items():
            value = getattr(row, column)
            if value is not None:
                series[name][row.date] = value
    return series


def _efficiency(activity: Activity) -> Optional[float]:
    """Metres per heartbeat."""
    if not (activity.distance_meters and activity.duration_seconds and activity.avg_heart_rate):
        return None
    metres_per_minute = activity.distance_meters / (activity.duration_seconds / 60)
    return metres_per_minute / activity.avg_heart_rate


def easy_runs(session: Session, user_id: int, end: date) -> list[tuple[date, float]]:
    """
    Runs of 20+ minutes at or slower than the athlete's own median pace -
    "easy" by their standard, not by a fixed pace. (date, efficiency),
    oldest first.
    """
    start = end - timedelta(days=EASY_RUN_LOOKBACK_DAYS)
    runs = session.exec(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            col(Activity.activity_type).in_(EASY_RUN_TYPES),
            Activity.start_time >= start,
            Activity.duration_seconds >= EASY_RUN_MIN_SECONDS,
        )
        .order_by(col(Activity.start_time))
    ).all()
    runs = [r for r in runs if r.start_time.date() <= end and _efficiency(r) is not None]
    if not runs:
        return []
    paces = [r.duration_seconds / r.distance_meters for r in runs]  # seconds per metre
    median_pace = statistics.median(paces)
    return [(r.start_time.date(), _efficiency(r)) for r, pace in zip(runs, paces) if pace >= median_pace]


def _weekly_minutes(session: Session, user_id: int, end: date) -> tuple[Optional[float], Optional[float]]:
    """
    Training minutes in the last 7 days, and the weekly average of the 28
    days before them. The average is None when the stored history does not
    reach back that far - a new user's "previous weeks" would otherwise be
    empty and every week would look like a jump.
    """
    first_day = session.exec(
        select(DailyMetric.date).where(DailyMetric.user_id == user_id).order_by(col(DailyMetric.date))
    ).first()
    start = end - timedelta(days=34)
    rows = session.exec(
        select(Activity.start_time, Activity.duration_seconds).where(
            Activity.user_id == user_id, Activity.start_time >= start
        )
    ).all()
    last, previous = 0.0, 0.0
    for start_time, seconds in rows:
        day = start_time.date()
        if day > end or not seconds:
            continue
        if day > end - timedelta(days=7):
            last += seconds / 60
        else:
            previous += seconds / 60
    if first_day is None or first_day > start:
        return last, None
    return last, previous / 4


def _latest_load(session: Session, user_id: int, end: date) -> tuple[Optional[float], Optional[float]]:
    row = session.exec(
        select(DailyMetric)
        .where(
            DailyMetric.user_id == user_id,
            DailyMetric.date >= end - timedelta(days=2),
            DailyMetric.date <= end,
            DailyMetric.training_load_acute.is_not(None),
            DailyMetric.training_load_chronic.is_not(None),
        )
        .order_by(col(DailyMetric.date).desc())
    ).first()
    return (row.training_load_acute, row.training_load_chronic) if row else (None, None)


# --- baselines -------------------------------------------------------------

def _store_baseline(session: Session, user_id: int, metric: str, base, start: date, end: date) -> None:
    values = dict(mean=base.mean, std=base.std, samples=base.samples, window_start=start, window_end=end)
    statement = (
        insert(MetricBaseline)
        .values(user_id=user_id, metric=metric, updated_at=clock.now_utc(), **values)
        .on_conflict_do_update(
            constraint="uq_metric_baseline_user_metric",
            set_={**values, "updated_at": clock.now_utc()},
        )
    )
    session.execute(statement)


# --- evaluation ------------------------------------------------------------

def evaluate(session: Session, user_id: int, end: Optional[date] = None, t=None) -> dict[str, rules.Finding]:
    """Run every rule for the days ending on `end` (default: today)."""
    end = end or clock.today()
    t = t or rules.Thresholds.from_settings()

    base_end = end - timedelta(days=JUDGED_DAYS)
    base_start = base_end - timedelta(days=BASELINE_DAYS - 1)
    history = _daily_series(session, user_id, base_start, base_end)
    recent = _daily_series(session, user_id, end - timedelta(days=JUDGED_DAYS - 1), end)

    baselines = {}
    for name, values in history.items():
        baselines[name] = rules.make_baseline(list(values.values()), t.min_baseline_days)
        if baselines[name] is not None:
            _store_baseline(session, user_id, name, baselines[name], base_start, base_end)

    runs = easy_runs(session, user_id, end)
    recent_cut = end - timedelta(days=EASY_RUN_RECENT_DAYS)
    recent_runs = [eff for day, eff in runs if day > recent_cut][-3:]
    earlier_runs = runs[: len(runs) - len(recent_runs)] if recent_runs else runs
    run_base = rules.make_baseline([eff for _, eff in earlier_runs], EASY_RUN_MIN_BASELINE_RUNS)
    if run_base is not None:
        _store_baseline(session, user_id, METRIC_EASY_RUN, run_base, earlier_runs[0][0], earlier_runs[-1][0])

    acute, chronic = _latest_load(session, user_id, end)
    last_week, previous_weekly = _weekly_minutes(session, user_id, end)
    session.commit()

    return {
        rules.RULE_HRV: rules.hrv_drop(recent["avg_hrv"], end, baselines["avg_hrv"], t),
        rules.RULE_RHR: rules.rhr_high(recent["resting_heart_rate"], end, baselines["resting_heart_rate"], t),
        rules.RULE_SLEEP: rules.sleep_poor(recent["sleep_duration_minutes"], recent["sleep_score"], end, t),
        rules.RULE_BODY_BATTERY: rules.body_battery_low(recent["body_battery_high"], end, t),
        rules.RULE_EASY_RUN: rules.easy_run_drift(recent_runs, run_base, t),
        rules.RULE_LOAD: rules.load_spike(acute, chronic, last_week, previous_weekly, t),
    }


def update_episodes(session: Session, user_id: int, findings: dict, end: date) -> None:
    open_alerts = {
        a.rule: a
        for a in session.exec(
            select(MetricAlert).where(MetricAlert.user_id == user_id, MetricAlert.ended_on.is_(None))
        ).all()
    }
    for rule, finding in findings.items():
        alert = open_alerts.get(rule)
        if finding.status == rules.FIRING:
            if alert is None:
                alert = session.exec(
                    select(MetricAlert)
                    .where(
                        MetricAlert.user_id == user_id,
                        MetricAlert.rule == rule,
                        MetricAlert.ended_on >= end - timedelta(days=REOPEN_WITHIN_DAYS),
                    )
                    .order_by(col(MetricAlert.ended_on).desc())
                ).first()
                if alert is not None:
                    alert.ended_on = None  # the same episode, back again
                else:
                    alert = MetricAlert(user_id=user_id, rule=rule, started_on=end, last_seen_on=end)
            alert.last_seen_on = max(alert.last_seen_on, end)
            alert.details = finding.details
            session.add(alert)
        elif alert is not None and (
            finding.status == rules.CLEAR or (end - alert.last_seen_on).days > STALE_AFTER_DAYS
        ):
            alert.ended_on = end
            session.add(alert)
    session.commit()


def announce(session: Session, user_id: int, end: date) -> Optional[CoachEvent]:
    """
    One event for every open episode not announced yet - several rules
    firing together are one message, not several. Keyed by day: if today's
    message has already been used, what is new waits for tomorrow (if it
    still holds by then).
    """
    pending = session.exec(
        select(MetricAlert)
        .where(
            MetricAlert.user_id == user_id,
            MetricAlert.ended_on.is_(None),
            MetricAlert.event_id.is_(None),
        )
        .order_by(col(MetricAlert.id))
    ).all()
    if not pending:
        return None
    event = record_event(
        session, user_id, KIND_ANOMALY, f"anomaly:{end.isoformat()}", {"alert_ids": [a.id for a in pending]}
    )
    if event is None:
        return None
    for alert in pending:
        alert.event_id = event.id
        session.add(alert)
    session.commit()
    return event


def check_user(session: Session, user_id: int) -> Optional[CoachEvent]:
    """The whole thing, as the sync calls it. Returns an event to deliver, if any."""
    end = clock.today()
    update_episodes(session, user_id, evaluate(session, user_id, end), end)
    return announce(session, user_id, end)


# --- what the coach can see ------------------------------------------------

def _alert_dict(alert: MetricAlert) -> dict:
    return {
        "rule": alert.rule,
        "started_on": alert.started_on.isoformat(),
        "last_seen_on": alert.last_seen_on.isoformat(),
        "ended_on": alert.ended_on.isoformat() if alert.ended_on else None,
        "details": alert.details,
    }


def health_overview(session: Session, user_id: int, days: int = 14) -> dict:
    """The athlete's own normal, and what stepped outside it lately."""
    since = clock.today() - timedelta(days=days)
    baselines = session.exec(select(MetricBaseline).where(MetricBaseline.user_id == user_id)).all()
    alerts = session.exec(
        select(MetricAlert)
        .where(MetricAlert.user_id == user_id, MetricAlert.last_seen_on >= since)
        .order_by(col(MetricAlert.started_on))
    ).all()
    return {
        "baselines": {
            b.metric: {"mean": round(b.mean, 2), "std": round(b.std, 2), "days": b.samples} for b in baselines
        },
        "open_alerts": [_alert_dict(a) for a in alerts if a.ended_on is None],
        "recently_ended": [_alert_dict(a) for a in alerts if a.ended_on is not None],
    }
