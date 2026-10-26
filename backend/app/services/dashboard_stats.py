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
from app.services.workout_stats import SPORT_KEYS, dashboard_sport, load_series, period_for

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


# The activity rings' fallbacks: Garmin's own defaults for a new account,
# used only until a synced day carries the person's real goal.
DEFAULT_STEP_GOAL = 7500
DEFAULT_INTENSITY_GOAL = 150
# The workouts ring: this week against the person's own usual week - the
# mean over the last AVERAGE_WEEKS full weeks - or this, with no history.
DEFAULT_WORKOUTS_GOAL = 3
AVERAGE_WEEKS = 8


def _counts_as_workout(activity_type: Optional[str]) -> bool:
    # Walking counts toward steps, not workouts: a daily walk to work would
    # otherwise close the workouts ring by Tuesday and say nothing.
    return dashboard_sport(activity_type) != "walking"


def _week(session: Session, user_id: int, today_: date) -> tuple[list[dict], list]:
    """
    This week, Sunday to Saturday: what was planned and what was done, one
    entry per day - the goals tab's week card and the home screen's strip.
    Also returns the week's activity rows (id, start, type, name, seconds,
    meters), so the home screen counts from the same query.
    """
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
        select(Activity.id, Activity.start_time, Activity.activity_type, Activity.activity_name,
               Activity.duration_seconds, Activity.distance_meters)
        .where(
            Activity.user_id == user_id,
            Activity.start_time >= week_start,
            Activity.start_time < week_end + timedelta(days=1),
        )
        .order_by(Activity.start_time)
    ).all()
    week = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        week.append({
            "date": day.isoformat(),
            "planned": [{"name": n, "sport": dashboard_sport(s), "on_watch": st == STATUS_SENT}
                        for d, n, s, st in planned if d == day],
            "done": [{"name": n or "", "sport": dashboard_sport(t), "minutes": round((sec or 0) / 60)}
                     for _id, st_, t, n, sec, _m in done if st_.date() == day],
        })
    return week, done


def _latest_goal(session: Session, user_id: int, column, through: date) -> Optional[int]:
    """The most recent goal Garmin sent on or before `through`, if it ever sent one."""
    return session.exec(
        select(column)
        .where(DailyMetric.user_id == user_id, DailyMetric.date <= through, column.is_not(None))
        .order_by(col(DailyMetric.date).desc())
    ).first()


def workouts_goal(session: Session, user_id: int, today_: date) -> int:
    """
    The person's usual week: mean workouts per week over the last
    AVERAGE_WEEKS full weeks, the current one left out (it isn't over).
    Someone newer than that is averaged over the full weeks they have;
    someone with none gets DEFAULT_WORKOUTS_GOAL. At least 1, so the ring
    can always close.
    """
    week_start, _ = period_for("week", 0, today_)
    first = session.exec(
        select(Activity.start_time).where(Activity.user_id == user_id).order_by(Activity.start_time)
    ).first()
    if first is None:
        return DEFAULT_WORKOUTS_GOAL
    first_week, _ = period_for("week", 0, first.date())
    start = max(first_week, week_start - timedelta(days=7 * AVERAGE_WEEKS))
    weeks = (week_start - start).days // 7
    if weeks <= 0:
        return DEFAULT_WORKOUTS_GOAL
    types = session.exec(
        select(Activity.activity_type).where(
            Activity.user_id == user_id, Activity.start_time >= start, Activity.start_time < week_start,
        )
    ).all()
    count = sum(1 for t in types if _counts_as_workout(t))
    # Half up, not Python's round-half-to-even: 2.5 a week is a 3-workout habit.
    return max(1, int(count / weeks + 0.5))


def activity_rings(session: Session, user_id: int, today_: date, week_done: list) -> dict:
    """
    The three rings, Apple Fitness style: steps against Garmin's step goal,
    the week's intensity minutes against Garmin's weekly goal, and the
    week's workouts against the person's usual week.

    Steps come in two views. The day: today's steps against today's goal.
    The week: steps so far against the sum of the seven daily goals, since
    Garmin's Auto Goal moves every day; a day without a stored goal (the
    rest of the week, a day the sync missed) takes the latest known one.
    """
    week_start, week_end = period_for("week", 0, today_)
    rows = {m.date: m for m in _metrics(session, user_id, week_start, today_)}
    step_goal = _latest_goal(session, user_id, DailyMetric.daily_step_goal, today_) or DEFAULT_STEP_GOAL
    intensity_goal = (
        _latest_goal(session, user_id, DailyMetric.intensity_minutes_goal, today_) or DEFAULT_INTENSITY_GOAL
    )

    def goal_on(day: date) -> int:
        row = rows.get(day)
        return row.daily_step_goal if row is not None and row.daily_step_goal else step_goal

    today_row = rows.get(today_)
    days = [week_start + timedelta(days=i) for i in range(7)]
    # Garmin's own counting: a vigorous minute is worth two.
    intensity = sum(
        (m.moderate_intensity_minutes or 0) + 2 * (m.vigorous_intensity_minutes or 0) for m in rows.values()
    )
    weeks, _ = current_streak_weeks(session, user_id, today_)
    return {
        "day": {"steps": (today_row.steps if today_row else None) or 0, "step_goal": goal_on(today_)},
        "week": {
            "steps": sum(m.steps or 0 for m in rows.values()),
            "step_goal": sum(goal_on(d) for d in days),
            "intensity_minutes": intensity,
            "intensity_goal": intensity_goal,
            "workouts": sum(1 for row in week_done if _counts_as_workout(row[2])),
            "workouts_goal": workouts_goal(session, user_id, today_),
        },
        "streak_weeks": weeks,
    }


# Form (Fitness - Fatigue) zones, the cut-offs the workouts tab's Form
# chart is read with: under -30 high load, -30..-10 building (the
# productive zone), -10..5 balanced, above 5 fresh.
LOAD_ZONE_TEXT = {
    # zone: (the state in a word, what to do about it)
    "high": ("עומס גבוה", "כדאי יום קל או מנוחה"),
    "building": ("בונה כושר", "עומס פרודוקטיבי, אפשר להמשיך כמתוכנן"),
    "balanced": ("מאוזן", "אפשר להעמיס היום"),
    "fresh": ("רענן", "אפשר להעמיס היום, יום טוב לאימון קשה"),
}


def load_zone(form: float) -> str:
    if form < -30:
        return "high"
    if form < -10:
        return "building"
    if form <= 5:
        return "balanced"
    return "fresh"


def load_balance(session: Session, user_id: int, today_: date) -> Optional[dict]:
    """Today's point of the workouts tab's Fitness/Fatigue lines, as a zone and an action."""
    days = load_series(session, user_id, "week", 0, today_)["days"]
    if not days:
        return None
    last = days[-1]
    zone = load_zone(last["form"])
    word, advice = LOAD_ZONE_TEXT[zone]
    return {"form": last["form"], "fitness": last["fitness"], "fatigue": last["fatigue"],
            "zone": zone, "word": word, "advice": advice}


def _hours(minutes: int) -> str:
    return f"{round(minutes / 30) / 2:g}"


def coach_line(rings: dict[str, dict], sleep_minutes: Optional[int], readiness: Optional[int],
               zone: Optional[str], planned: list[dict], done: list[dict]) -> Optional[str]:
    """
    One sentence for the top of the home screen: why today looks the way it
    does, and what that means for today's plan. Rule-based on purpose - it
    is read every morning and has to be instant and predictable, not a
    model call. At most two reasons, worst news first.
    """
    if done:
        name = done[-1]["name"]
        return f"{name or 'האימון של היום'} כבר מאחוריך, עכשיו זמן להתאושש: שתייה, אוכל ושינה טובה"

    state = {key: ring["state"] for key, ring in rings.items()}
    bad, good = [], []
    if state.get("rhr") == "low":  # "low" is the ring's verdict; for resting HR that means it's high
        bad.append("דופק המנוחה גבוה מהרגיל")
    if state.get("hrv") == "low":
        bad.append("ה-HRV נמוך מהרגיל")
    if sleep_minutes is not None and sleep_minutes < 360:
        bad.append(f"ישנת רק {_hours(sleep_minutes)} שעות")
    elif state.get("sleep") == "low":
        bad.append("השינה הייתה חלשה")
    if zone == "high":
        bad.append("העומס המצטבר גבוה")
    if state.get("body_battery") == "low":
        bad.append("ה-Body Battery נמוך")
    if readiness is not None and readiness < 25:
        bad.append("ציון המוכנות נמוך")

    if state.get("hrv") == "good":
        good.append("ה-HRV מעל הרגיל")
    if sleep_minutes is not None and sleep_minutes >= 420:
        good.append(f"ישנת {_hours(sleep_minutes)} שעות")
    elif state.get("sleep") == "good":
        good.append("ישנת טוב")
    if state.get("body_battery") == "good":
        good.append("ה-Body Battery גבוה")
    if state.get("rhr") == "good":
        good.append("דופק המנוחה נמוך מהרגיל")
    if zone == "fresh":
        good.append("הגוף רענן מעומס")

    def joined(reasons: list[str]) -> str:
        # "ו" glues straight onto the second phrase: "...וישנת", "...וה-HRV".
        return reasons[0] if len(reasons) == 1 else f"{reasons[0]} ו{reasons[1]}"

    hard = any(
        w["sport"] != "strength" and any(b.get("kind") == "interval" for b in w.get("blocks") or [])
        for w in planned
    )
    strength = any(w["sport"] == "strength" for w in planned)

    if bad:
        if hard:
            return f"{joined(bad)}, כדאי להוריד עצימות באימון של היום"
        if planned:
            return f"{joined(bad)}, כדאי לקחת את האימון של היום בקלות"
        return f"{joined(bad)}, כדאי יום קל"
    if good:
        if hard:
            return f"{joined(good)}, יום טוב לאינטרוולים"
        if strength:
            return f"{joined(good)}, יום טוב לאימון הכוח"
        if planned:
            return f"{joined(good)}, יום טוב לצאת לאימון של היום"
        return f"{joined(good)}, יום טוב לאימון איכותי"
    if all(s == "unknown" for s in state.values()) and sleep_minutes is None:
        return "אין עדיין נתונים מהשעון להיום, אפשר לצאת לאימון כמתוכנן" if planned else None
    if planned:
        return "המדדים בטווח הרגיל, אפשר לצאת לאימון כמתוכנן"
    return "המדדים בטווח הרגיל, יום טוב לאימון קל או מתון"


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
    workouts = [
        {
            "name": w.name,
            "sport": dashboard_sport(w.sport),
            "on_watch": w.status == STATUS_SENT,
            "blocks": _workout_blocks(w.spec or {}, w.sport),
        }
        for w in planned
    ]
    next_planned = session.exec(
        select(StoredWorkout.name, StoredWorkout.sport, StoredWorkout.scheduled_for, StoredWorkout.status)
        .where(
            StoredWorkout.user_id == user_id,
            StoredWorkout.scheduled_for > today_,
            StoredWorkout.status.in_([STATUS_PLANNED, STATUS_SENT]),
        )
        .order_by(StoredWorkout.scheduled_for)
    ).first()

    week, week_done = _week(session, user_id, today_)
    today_done = [
        {"id": id_, "name": name or "", "sport": dashboard_sport(t), "seconds": round(sec or 0),
         "meters": round(meters) if meters else None}
        for id_, start, t, name, sec, meters in week_done if start.date() == today_
    ]

    weeks, _ = current_streak_weeks(session, user_id, today_)
    readiness, _ = _latest(metrics, "training_readiness_score")
    # Last night only: an older night says nothing about this morning.
    sleep_minutes, sleep_on = _latest(metrics, "sleep_duration_minutes")
    if sleep_on is not None and sleep_on < today_ - timedelta(days=1):
        sleep_minutes = None
    balance = load_balance(session, user_id, today_)

    return {
        "rings": rings,
        "readiness_score": readiness,
        "coach_line": coach_line(
            {r["key"]: r for r in rings}, sleep_minutes, readiness,
            balance["zone"] if balance else None, workouts, today_done,
        ),
        "alert": None if alert is None else {
            "rule": alert.rule,
            "text": RULE_TEXT.get(alert.rule, alert.rule),
            "since": alert.started_on.isoformat(),
            "metric": next((m for m, r in RULE_FOR_METRIC.items() if r == alert.rule), None),
        },
        "workouts": workouts,
        "today_done": today_done,
        "next_workout": None if next_planned is None else {
            "name": next_planned.name,
            "sport": dashboard_sport(next_planned.sport),
            "date": next_planned.scheduled_for.isoformat(),
            "on_watch": next_planned.status == STATUS_SENT,
        },
        "week": week,
        "activity_rings": activity_rings(session, user_id, today_, week_done),
        "load_balance": balance,
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

    week, _ = _week(session, user_id, today_)

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
