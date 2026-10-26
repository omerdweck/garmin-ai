"""
The numbers behind the Mini App's today, health, goals and records tabs.
Read-only, like workout_stats.py (the workouts tab), and computed in Python
so the page only draws.

Everything comes from tables the bot already fills: daily_metric (one row a
day, a year of it after the history import), metric_baseline and
metric_alert (what the anomaly rules decided), activity, personal_record,
training_plan and workout.
"""

from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import defer
from sqlmodel import Session, col, select

from app.models.activity import Activity
from app.models.daily_metric import DailyMetric
from app.models.metric_alert import MetricAlert, MetricBaseline
from app.models.personal_record import PersonalRecord
from app.models.training_plan import TrainingPlan
from app.models.workout import STATUS_PLANNED, STATUS_SENT, StoredWorkout
from app.services import anomaly_rules as rules
from app.services.achievements import (
    DISTANCE_MILESTONES_KM,
    STREAK_MIN_WORKOUTS,
    WORKOUT_COUNT_MILESTONES,
    current_streak_weeks,
    sport_family,
)
from app.services.records import format_value, record_meta
from app.services.workout_schema import DEFAULT_SECONDS_PER_KM
from app.services.workout_stats import SPORT_KEYS, dashboard_sport, period_for

# --- shared -------------------------------------------------------------------

# The anomaly rule that watches each metric, so the chart can mark the days
# the coach raised an alert about it.
RULE_FOR_METRIC = {
    "avg_hrv": rules.RULE_HRV,
    "resting_heart_rate": rules.RULE_RHR,
    "body_battery_high": rules.RULE_BODY_BATTERY,
    "sleep_score": rules.RULE_SLEEP,
}

RULE_TEXT = {
    rules.RULE_HRV: "ה-HRV ירד כמה ימים ברצף",
    rules.RULE_RHR: "דופק המנוחה גבוה מהרגיל",
    rules.RULE_SLEEP: "כמה לילות של שינה חלשה",
    rules.RULE_BODY_BATTERY: "ה-Body Battery לא נטען כרגיל",
    rules.RULE_EASY_RUN: "ריצות קלות דורשות יותר דופק מהרגיל",
    rules.RULE_LOAD: "קפיצה חדה בעומס האימונים",
}


def _metrics(session: Session, user_id: int, start: date, end: date) -> list[DailyMetric]:
    # raw_json left behind: a year of it is megabytes (the whole night's
    # sleep data per row), and nothing here needs it.
    return session.exec(
        select(DailyMetric)
        .options(defer(DailyMetric.raw_json))
        .where(DailyMetric.user_id == user_id, DailyMetric.date >= start, DailyMetric.date <= end)
        .order_by(DailyMetric.date)
    ).all()


def _baselines(session: Session, user_id: int) -> dict[str, MetricBaseline]:
    return {b.metric: b for b in session.exec(select(MetricBaseline).where(MetricBaseline.user_id == user_id))}


def _band(baseline: Optional[MetricBaseline]) -> Optional[dict]:
    if baseline is None or baseline.samples < 7:
        return None
    return {
        "mean": round(baseline.mean, 1),
        "low": round(baseline.mean - baseline.std, 1),
        "high": round(baseline.mean + baseline.std, 1),
    }


def _latest(metrics: list[DailyMetric], field: str):
    for m in reversed(metrics):
        value = getattr(m, field)
        if value is not None:
            return value, m.date
    return None, None


# --- today --------------------------------------------------------------------


def _ring(key: str, label: str, value, as_of, scale_max: Optional[float], band: Optional[dict], higher_is_better: bool) -> dict:
    """
    One readiness ring. `fill` 0..1 is how full it's drawn; `state` is how it
    compares with this person's own normal: good, normal, low.
    Body Battery and sleep score are 0-100 already. HRV and resting HR mean
    nothing on an absolute scale, so they're placed against the personal band.
    """
    state, fill = "unknown", 0.0
    if value is not None and scale_max:
        fill = value / scale_max
        state = "good" if value >= 0.75 * scale_max else "low" if value < 0.45 * scale_max else "normal"
    elif value is not None and band:
        spread = max(band["high"] - band["mean"], 0.5)
        z = (value - band["mean"]) / spread * (1 if higher_is_better else -1)
        fill = 0.6 + 0.2 * z
        state = "good" if z >= 0.5 else "low" if z <= -1 else "normal"
    elif value is not None:
        state, fill = "normal", 0.6
    return {
        "key": key, "label": label, "value": value, "as_of": as_of.isoformat() if as_of else None,
        "fill": round(min(1.0, max(0.0, fill)), 2), "state": state, "band": band,
    }


def _workout_blocks(spec: dict, sport: str) -> list[dict]:
    """The planned workout as blocks: what kind, roughly how long - for the intensity strip."""
    blocks: list[dict] = []

    def seconds_of(step: dict) -> int:
        if step.get("seconds"):
            return int(step["seconds"])
        if step.get("meters"):
            return int(step["meters"] / 1000 * DEFAULT_SECONDS_PER_KM.get(sport, 360))
        return 60

    for item in spec.get("steps") or []:
        kind = item.get("kind")
        if kind == "repeat":
            for _ in range(int(item.get("times") or 1)):
                for step in item.get("steps") or []:
                    blocks.append({"kind": step.get("kind"), "seconds": seconds_of(step)})
        elif kind == "exercise":
            per_set = int(item.get("seconds") or (item.get("reps") or 10) * 4)
            sets = int(item.get("sets") or 1)
            blocks.append({"kind": "interval", "seconds": sets * per_set, "label": item.get("exercise")})
            blocks.append({"kind": "rest", "seconds": sets * int(item.get("rest_seconds") or 60)})
        else:
            blocks.append({"kind": kind, "seconds": seconds_of(item)})
    return blocks[:80]


def today(session: Session, user_id: int, today_: date) -> dict:
    metrics = _metrics(session, user_id, today_ - timedelta(days=3), today_)
    bands = {k: _band(v) for k, v in _baselines(session, user_id).items()}

    rings = []
    value, as_of = _latest(metrics, "body_battery_high")
    rings.append(_ring("body_battery", "Body Battery", value, as_of, 100, None, True))
    value, as_of = _latest(metrics, "avg_hrv")
    rings.append(_ring("hrv", "HRV", value, as_of, None, bands.get("avg_hrv"), True))
    value, as_of = _latest(metrics, "sleep_score")
    rings.append(_ring("sleep", "שינה", value, as_of, 100, None, True))
    value, as_of = _latest(metrics, "resting_heart_rate")
    rings.append(_ring("rhr", "דופק מנוחה", value, as_of, None, bands.get("resting_heart_rate"), False))

    alert = session.exec(
        select(MetricAlert)
        .where(MetricAlert.user_id == user_id, MetricAlert.ended_on.is_(None))
        .order_by(col(MetricAlert.last_seen_on).desc())
    ).first()

    planned = session.exec(
        select(StoredWorkout).where(
            StoredWorkout.user_id == user_id,
            StoredWorkout.scheduled_for == today_,
            StoredWorkout.status.in_([STATUS_PLANNED, STATUS_SENT]),
        )
    ).all()

    weeks, _ = current_streak_weeks(session, user_id, today_)
    readiness, _ = _latest(metrics, "training_readiness_score")

    return {
        "rings": rings,
        "readiness_score": readiness,
        "alert": None if alert is None else {
            "rule": alert.rule,
            "text": RULE_TEXT.get(alert.rule, alert.rule),
            "since": alert.started_on.isoformat(),
            "metric": next((m for m, r in RULE_FOR_METRIC.items() if r == alert.rule), None),
        },
        "workouts": [
            {
                "name": w.name,
                "sport": dashboard_sport(w.sport),
                "on_watch": w.status == STATUS_SENT,
                "blocks": _workout_blocks(w.spec or {}, w.sport),
            }
            for w in planned
        ],
        "streak_weeks": weeks,
        "streak_rule": STREAK_MIN_WORKOUTS,
    }


# --- health -------------------------------------------------------------------

HEALTH_METRICS = [
    # key, label, how to get it from a row
    ("resting_heart_rate", lambda m: m.resting_heart_rate),
    ("avg_hrv", lambda m: m.avg_hrv),
    ("avg_stress_level", lambda m: m.avg_stress_level),
    ("body_battery_high", lambda m: m.body_battery_high),
    ("intensity_minutes", lambda m: (
        None if m.moderate_intensity_minutes is None and m.vigorous_intensity_minutes is None
        else (m.moderate_intensity_minutes or 0) + 2 * (m.vigorous_intensity_minutes or 0)
    )),
    ("respiration", lambda m: m.avg_sleep_respiration),
    ("spo2", lambda m: m.avg_spo2),
]


def health(session: Session, user_id: int, kind: str, offset: int, today_: date) -> dict:
    start, end = period_for(kind, offset, today_)
    end = min(end, today_)
    rows = _metrics(session, user_id, start, end)
    bands = {k: _band(v) for k, v in _baselines(session, user_id).items()}

    # "Last full week" in each card, as in the video: the Sunday-Saturday week before this one.
    week_start, week_end = period_for("week", 1, today_)
    last_week = _metrics(session, user_id, week_start, week_end)

    alerts = session.exec(
        select(MetricAlert).where(
            MetricAlert.user_id == user_id,
            MetricAlert.started_on <= end,
            (MetricAlert.ended_on.is_(None)) | (MetricAlert.ended_on >= start),
        )
    ).all()

    metrics = {}
    for key, get in HEALTH_METRICS:
        points = [[m.date.isoformat(), get(m)] for m in rows if get(m) is not None]
        week_values = [get(m) for m in last_week if get(m) is not None]
        rule = RULE_FOR_METRIC.get(key)
        metrics[key] = {
            "days": points,
            "last_week": round(sum(week_values) / len(week_values), 1) if week_values else None,
            "band": bands.get(key),
            "alerts": [
                {"start": max(a.started_on, start).isoformat(), "end": min(a.ended_on or a.last_seen_on, end).isoformat()}
                for a in alerts if rule and a.rule == rule
            ],
        }

    sleep = [
        {
            "date": m.date.isoformat(),
            "deep": m.deep_sleep_minutes or 0,
            "light": m.light_sleep_minutes or 0,
            "rem": m.rem_sleep_minutes or 0,
            "awake": m.awake_minutes or 0,
            "score": m.sleep_score,
            "bed": m.sleep_start_local.strftime("%H:%M") if m.sleep_start_local else None,
            "wake": m.sleep_end_local.strftime("%H:%M") if m.sleep_end_local else None,
        }
        for m in rows if m.sleep_duration_minutes
    ]
    sleep_week = [m.sleep_duration_minutes for m in last_week if m.sleep_duration_minutes]

    first = session.exec(
        select(DailyMetric.date).where(DailyMetric.user_id == user_id).order_by(DailyMetric.date)
    ).first()
    recorded = len(session.exec(select(DailyMetric.id).where(DailyMetric.user_id == user_id)).all())

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days_recorded": recorded,
        "since": first.isoformat() if first else None,
        "metrics": metrics,
        "sleep": sleep,
        "sleep_last_week_hours": round(sum(sleep_week) / len(sleep_week) / 60, 1) if sleep_week else None,
        "sleep_alerts": [
            {"start": max(a.started_on, start).isoformat(), "end": min(a.ended_on or a.last_seen_on, end).isoformat()}
            for a in alerts if a.rule == rules.RULE_SLEEP
        ],
    }


# --- goals --------------------------------------------------------------------

RACES = [
    ("5k", "race_predict_5k_seconds", '5 ק"מ'),
    ("10k", "race_predict_10k_seconds", '10 ק"מ'),
    ("half", "race_predict_half_seconds", "חצי מרתון"),
    ("marathon", "race_predict_marathon_seconds", "מרתון"),
]


def goals(session: Session, user_id: int, today_: date) -> dict:
    rows = _metrics(session, user_id, today_ - timedelta(days=364), today_)

    races = []
    for key, field, label in RACES:
        points = [[m.date.isoformat(), getattr(m, field)] for m in rows if getattr(m, field)]
        if points:
            races.append({
                "key": key, "label": label, "days": points,
                "now": points[-1][1],
                # Against ~3 months ago - long enough for a real trend, not day-to-day noise.
                "before": next(
                    (p[1] for p in reversed(points) if p[0] <= (today_ - timedelta(days=90)).isoformat()), points[0][1]
                ),
            })

    vo2 = [[m.date.isoformat(), m.vo2_max] for m in rows if m.vo2_max]

    # This week, Sunday to Saturday: what was planned and what was done.
    week_start, week_end = period_for("week", 0, today_)
    planned = session.exec(
        select(StoredWorkout.scheduled_for, StoredWorkout.name, StoredWorkout.sport, StoredWorkout.status).where(
            StoredWorkout.user_id == user_id,
            StoredWorkout.scheduled_for >= week_start,
            StoredWorkout.scheduled_for <= week_end,
            StoredWorkout.status.in_([STATUS_PLANNED, STATUS_SENT]),
        )
    ).all()
    done = session.exec(
        select(Activity.start_time, Activity.activity_type, Activity.activity_name, Activity.duration_seconds).where(
            Activity.user_id == user_id,
            Activity.start_time >= week_start,
            Activity.start_time < week_end + timedelta(days=1),
        )
    ).all()
    week = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        week.append({
            "date": day.isoformat(),
            "planned": [{"name": n, "sport": dashboard_sport(s), "on_watch": st == STATUS_SENT}
                        for d, n, s, st in planned if d == day],
            "done": [{"name": n or "", "sport": dashboard_sport(t), "minutes": round((sec or 0) / 60)}
                     for st_, t, n, sec in done if st_.date() == day],
        })

    plans = session.exec(
        select(TrainingPlan.discipline, TrainingPlan.updated_at).where(
            TrainingPlan.user_id == user_id, TrainingPlan.is_active == True  # noqa: E712
        )
    ).all()

    return {
        "races": races,
        "vo2max": vo2,
        "week": week,
        "plans": [{"discipline": d, "updated": u.date().isoformat()} for d, u in plans],
    }


# --- records ------------------------------------------------------------------

FAMILY_LABEL = {"running": "ריצה", "cycling": "רכיבה", "swimming": "שחייה", "strength": "כוח"}


def records(session: Session, user_id: int, today_: date) -> dict:
    prs = []
    for row in session.exec(select(PersonalRecord).where(PersonalRecord.user_id == user_id)):
        meta = record_meta(row.record_type)
        if meta is None:
            continue
        prs.append({
            "key": row.record_type, "sport": meta[1], "label": meta[2],
            "value": format_value(row.record_type, row.value),
            "date": row.achieved_at.isoformat() if row.achieved_at else None,
        })

    activities = session.exec(
        select(Activity.start_time, Activity.activity_type, Activity.distance_meters, Activity.duration_seconds,
               Activity.activity_name)
        .where(Activity.user_id == user_id)
        .order_by(Activity.start_time)
    ).all()

    # Milestones are recomputed from the workouts themselves rather than read
    # from the achievement table: that table only holds what was celebrated
    # since the bot started, and the imported year would be missing from it.
    totals: dict[str, float] = defaultdict(float)
    reached: list[dict] = []
    for i, (start, activity_type, meters, _sec, _name) in enumerate(activities, start=1):
        family = sport_family(activity_type)
        if family in DISTANCE_MILESTONES_KM and meters:
            before = totals[family]
            totals[family] += meters / 1000
            for km in DISTANCE_MILESTONES_KM[family]:
                if before < km <= totals[family]:
                    reached.append({"icon": "📏", "text": f'{km:,} ק"מ של {FAMILY_LABEL[family]}', "date": start.date().isoformat()})
        if i in WORKOUT_COUNT_MILESTONES:
            reached.append({"icon": "🎯", "text": f"אימון מספר {i}", "date": start.date().isoformat()})

    next_up = []
    for family, milestones in DISTANCE_MILESTONES_KM.items():
        upcoming = next((km for km in milestones if km > totals[family]), None)
        if upcoming and totals[family] > 0:
            next_up.append({"icon": "📏", "text": f'{upcoming:,} ק"מ של {FAMILY_LABEL[family]}',
                            "progress": round(totals[family]), "target": upcoming})
    count_next = next((c for c in WORKOUT_COUNT_MILESTONES if c > len(activities)), None)
    if count_next:
        next_up.append({"icon": "🎯", "text": f"אימון מספר {count_next}", "progress": len(activities), "target": count_next})

    weeks, _ = current_streak_weeks(session, user_id, today_)

    # "My year": the last 365 days.
    year_start = today_ - timedelta(days=364)
    year = [a for a in activities if a[0].date() >= year_start]
    hours_by_sport: dict[str, float] = defaultdict(float)
    hours_by_month: dict[str, float] = defaultdict(float)
    for start, activity_type, _m, sec, _n in year:
        hours_by_sport[dashboard_sport(activity_type)] += (sec or 0) / 3600
        hours_by_month[start.strftime("%Y-%m")] += (sec or 0) / 3600
    longest = max(year, key=lambda a: a[3] or 0, default=None)
    farthest = max(year, key=lambda a: a[2] or 0, default=None)

    return {
        "personal_records": prs,
        "reached": list(reversed(reached)),
        "next": next_up,
        "streak_weeks": weeks,
        "streak_rule": STREAK_MIN_WORKOUTS,
        "year": {
            "workouts": len(year),
            "km": round(sum((a[2] or 0) for a in year) / 1000),
            "hours": round(sum((a[3] or 0) for a in year) / 3600),
            "top_sport": max(hours_by_sport, key=hours_by_sport.get) if hours_by_sport else None,
            "hours_by_sport": {k: round(hours_by_sport.get(k, 0), 1) for k in SPORT_KEYS},
            "best_month": max(hours_by_month, key=hours_by_month.get) if hours_by_month else None,
            "best_month_hours": round(max(hours_by_month.values()), 1) if hours_by_month else 0,
            "longest": None if not longest else {
                "name": longest[4] or "", "minutes": round((longest[3] or 0) / 60), "date": longest[0].date().isoformat()},
            "farthest": None if not farthest or not farthest[2] else {
                "name": farthest[4] or "", "km": round(farthest[2] / 1000, 1), "date": farthest[0].date().isoformat()},
        },
    }
