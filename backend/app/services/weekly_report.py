"""
The facts behind the weekly summary, computed rather than narrated.

Split from the message itself for the same reason the quick-lookup buttons
never call Claude: these numbers are already exact, and paying a model to
retype them would be spending tokens to lose precision. What the model adds
is the reading - which of eight signals matter this week and what to do
about it - and that is the only part it is given.

Everything here is week-over-week. A week's numbers in isolation say very
little: 8,000 steps a day is unremarkable until you know last week was
11,000.
"""

from datetime import date as date_type
from datetime import timedelta
from typing import Optional

from sqlmodel import Session, col, func, select

from app.models.activity import Activity
from app.models.daily_metric import DailyMetric
from app.models.plan_session import PlanSession
from app.models.training_plan import TrainingPlan

# The week the summary covers ends on the day it is sent, so a Saturday
# evening report describes Sunday-to-Saturday - the Israeli week these users
# actually train in.
WEEK_DAYS = 7


def _window(end: date_type, weeks_back: int = 0) -> tuple[date_type, date_type]:
    stop = end - timedelta(days=WEEK_DAYS * weeks_back)
    return stop - timedelta(days=WEEK_DAYS - 1), stop


def _metric_avg(session: Session, user_id: int, field: str, start: date_type, end: date_type) -> Optional[float]:
    column = getattr(DailyMetric, field)
    value = session.exec(
        select(func.avg(column)).where(
            DailyMetric.user_id == user_id,
            DailyMetric.date >= start,
            DailyMetric.date <= end,
            col(column).is_not(None),
        )
    ).one()
    return round(float(value), 1) if value is not None else None


def _metric_sum(session: Session, user_id: int, field: str, start: date_type, end: date_type) -> Optional[int]:
    column = getattr(DailyMetric, field)
    value = session.exec(
        select(func.sum(column)).where(
            DailyMetric.user_id == user_id,
            DailyMetric.date >= start,
            DailyMetric.date <= end,
        )
    ).one()
    return int(value) if value is not None else None


def _activities(session: Session, user_id: int, start: date_type, end: date_type) -> list[Activity]:
    return list(
        session.exec(
            select(Activity).where(
                Activity.user_id == user_id,
                col(Activity.start_time) >= start,
                col(Activity.start_time) < end + timedelta(days=1),
            )
        ).all()
    )


def _workout_block(activities: list[Activity]) -> dict:
    by_type: dict[str, dict] = {}
    for item in activities:
        entry = by_type.setdefault(item.activity_type or "other", {"count": 0, "minutes": 0, "km": 0.0})
        entry["count"] += 1
        entry["minutes"] += int((item.duration_seconds or 0) // 60)
        entry["km"] += (item.distance_meters or 0) / 1000

    return {
        "count": len(activities),
        "minutes": sum(b["minutes"] for b in by_type.values()),
        "km": round(sum(b["km"] for b in by_type.values()), 1),
        "by_type": {k: {**v, "km": round(v["km"], 1)} for k, v in by_type.items()},
    }


def _plan_adherence(session: Session, user_id: int, activities: list[Activity]) -> Optional[dict]:
    """
    Sessions the approved plans prescribed for a week against workouts
    actually recorded, counted per discipline.

    Matched by count, not by pairing each planned session to a specific
    workout: a run done on Tuesday instead of Monday is the plan being
    followed, and a matcher strict enough to say otherwise would report
    failure for something nobody would call one.
    """
    plans = session.exec(
        select(TrainingPlan).where(
            TrainingPlan.user_id == user_id,
            TrainingPlan.is_active == True,  # noqa: E712
        )
    ).all()
    if not plans:
        return None

    planned: dict[str, int] = {}
    for plan in plans:
        sessions = session.exec(
            select(PlanSession).where(PlanSession.plan_id == plan.id, PlanSession.is_rest == False)  # noqa: E712
        ).all()
        planned[plan.discipline] = len(sessions)

    # Plan disciplines are our own vocabulary; activity types are Garmin's.
    ALIASES = {
        "running": {"running", "treadmill_running", "trail_running"},
        "swimming": {"lap_swimming", "open_water_swimming"},
        "cycling": {"cycling", "indoor_cycling"},
        "strength": {"strength_training"},
    }

    result = {}
    for discipline, target in planned.items():
        types = ALIASES.get(discipline, {discipline})
        done = sum(1 for a in activities if (a.activity_type or "") in types)
        result[discipline] = {"planned": target, "done": done}
    return result


def build_weekly_facts(session: Session, user_id: int, end: Optional[date_type] = None) -> dict:
    """
    Everything the weekly summary is allowed to state, with last week beside
    it. Values Garmin never recorded are absent rather than zero - a week
    the watch was off did not have zero sleep.
    """
    end = end or date_type.today()
    this_start, this_end = _window(end, 0)
    prev_start, prev_end = _window(end, 1)

    this_activities = _activities(session, user_id, this_start, this_end)
    prev_activities = _activities(session, user_id, prev_start, prev_end)

    def pair(fn, field=None):
        if field:
            return {"this_week": fn(session, user_id, field, this_start, this_end),
                    "last_week": fn(session, user_id, field, prev_start, prev_end)}
        return {"this_week": fn(this_activities), "last_week": fn(prev_activities)}

    facts = {
        "week": {"from": this_start.isoformat(), "to": this_end.isoformat()},
        "workouts": pair(_workout_block),
        "steps": pair(_metric_sum, "steps"),
        "sleep_minutes": pair(_metric_avg, "sleep_duration_minutes"),
        "resting_heart_rate": pair(_metric_avg, "resting_heart_rate"),
        "hrv": pair(_metric_avg, "avg_hrv"),
        "training_load_acute": pair(_metric_avg, "training_load_acute"),
        "training_load_chronic": pair(_metric_avg, "training_load_chronic"),
    }

    # Race predictions move on a weekly timescale, which is exactly why they
    # belong here rather than in the daily message.
    for field, key in (
        ("race_predict_5k_seconds", "race_5k_seconds"),
        ("race_predict_10k_seconds", "race_10k_seconds"),
    ):
        block = pair(_metric_avg, field)
        if block["this_week"] is not None:
            facts[key] = {k: int(v) if v is not None else None for k, v in block.items()}

    vo2 = _metric_avg(session, user_id, "vo2_max", this_start, this_end)
    if vo2 is not None:
        facts["vo2_max"] = {"this_week": vo2,
                            "last_week": _metric_avg(session, user_id, "vo2_max", prev_start, prev_end)}

    adherence = _plan_adherence(session, user_id, this_activities)
    if adherence:
        facts["plan_adherence"] = adherence

    return facts


RLM = "‏"

DISCIPLINE_HE = {
    "running": "ריצה", "swimming": "שחייה", "cycling": "אופניים",
    "strength": "כוח", "general": "כללי",
}

TYPE_HE = {
    "running": "🏃 ריצה", "treadmill_running": "🏃 ריצה", "trail_running": "⛰️ שטח",
    "lap_swimming": "🏊 שחייה", "open_water_swimming": "🏊 שחייה",
    "cycling": "🚴 אופניים", "indoor_cycling": "🚴 אופניים",
    "strength_training": "🏋️ כוח", "walking": "🚶 הליכה", "cardio": "💪 קרדיו",
}


def _rtl(text: str) -> str:
    return f"{RLM}{text}"


def _delta(current, previous, unit: str = "", lower_is_better: bool = False) -> str:
    """
    "(+12%)" style comparison, or "" when there is nothing to compare against.

    Direction is stated rather than left to an arrow: for resting heart rate
    a fall is an improvement and a rise is not, and an arrow means the
    opposite thing in each case.
    """
    if current is None or previous is None or previous == 0:
        return ""
    diff = current - previous
    if abs(diff) < previous * 0.02:
        return _rtl("  ·  ללא שינוי")
    good = (diff < 0) if lower_is_better else (diff > 0)
    mark = "🟢" if good else "🔻"
    return f"  ·  {mark} {'+' if diff > 0 else '−'}{abs(diff):,.0f}{unit} מהשבוע שעבר"


def _race_time(seconds) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_weekly_facts(facts: dict) -> str:
    """The numbers block. Everything here is measured; nothing is inferred."""
    w, lw = facts["workouts"]["this_week"], facts["workouts"]["last_week"]

    lines = [
        _rtl("📅 *סיכום שבועי*"),
        _rtl(f"_{facts['week']['from']} — {facts['week']['to']}_"),
        "",
        _rtl(f"🏃 *{w['count']}* אימונים · *{w['minutes']}* דקות"
             + (f" · *{w['km']:g}* ק\"מ" if w["km"] else "")),
    ]
    if lw["count"] or w["count"]:
        lines.append(_rtl(f"_בשבוע שעבר: {lw['count']} אימונים, {lw['minutes']} דקות_"))

    if w["by_type"]:
        lines.append("")
        for key, block in sorted(w["by_type"].items(), key=lambda kv: -kv[1]["count"]):
            extra = f" · {block['km']:g} ק\"מ" if block["km"] else ""
            lines.append(_rtl(f"   {TYPE_HE.get(key, key)} — {block['count']}× · {block['minutes']} דק'{extra}"))

    if "plan_adherence" in facts:
        lines += ["", _rtl("*מול התוכנית*")]
        for discipline, block in facts["plan_adherence"].items():
            mark = "✅" if block["done"] >= block["planned"] else "⚠️"
            lines.append(_rtl(f"   {mark} {DISCIPLINE_HE.get(discipline, discipline)} — "
                              f"{block['done']} מתוך {block['planned']}"))

    lines.append("")
    sleep = facts["sleep_minutes"]
    if sleep["this_week"] is not None:
        hours, mins = divmod(int(sleep["this_week"]), 60)
        lines.append(_rtl(f"😴 שינה ממוצעת — *{hours}:{mins:02d}*")
                     + _delta(sleep["this_week"], sleep["last_week"], " דק'"))

    rhr = facts["resting_heart_rate"]
    if rhr["this_week"] is not None:
        lines.append(_rtl(f"❤️ דופק מנוחה — *{rhr['this_week']:.0f}*")
                     + _delta(rhr["this_week"], rhr["last_week"], "", lower_is_better=True))

    load_a, load_c = facts["training_load_acute"], facts["training_load_chronic"]
    if load_a["this_week"] is not None and load_c["this_week"]:
        lines.append(_rtl(f"📈 עומס — *{load_a['this_week']:.0f}* מול בסיס *{load_c['this_week']:.0f}*"))

    if "race_10k_seconds" in facts:
        r = facts["race_10k_seconds"]
        note = ""
        if r["last_week"]:
            diff = r["last_week"] - r["this_week"]
            if abs(diff) >= 2:
                note = f"  ·  {'🟢 מהר יותר' if diff > 0 else '🔻 איטי יותר'} ב-{_race_time(abs(diff))}"
        lines.append(_rtl(f"🏁 תחזית 10 ק\"מ — *{_race_time(r['this_week'])}*") + note)

    return "\n".join(lines)
