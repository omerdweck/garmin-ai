"""
Rewards, decided entirely in Python - no AI, so they cost nothing and say
exactly what happened.

Every milestone is judged by the workout that *crossed* it: total distance
before this workout below the line, total after it at or above. That one
rule is what keeps a new user from being showered with "100 km!", "500 km!"
the first time a workout arrives after their month of history was imported -
those lines were crossed long ago, by workouts that are already in the past.
"""

from datetime import timedelta
from typing import Optional

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, func, select

from app.core import clock
from app.models.achievement import Achievement
from app.models.activity import Activity
from app.models.plan_session import week_index

# Garmin activity types grouped into the sports a person thinks in.
SPORT_FAMILIES = {
    "running": {"running", "trail_running", "treadmill_running", "track_running", "indoor_running"},
    "cycling": {"cycling", "road_biking", "mountain_biking", "indoor_cycling", "virtual_ride", "gravel_cycling"},
    "swimming": {"lap_swimming", "open_water_swimming", "swimming"},
    "strength": {"strength_training"},
}

DISTANCE_MILESTONES_KM = {
    "running": [50, 100, 250, 500, 1000, 2000, 5000],
    "cycling": [100, 500, 1000, 2500, 5000, 10000],
    "swimming": [10, 25, 50, 100, 250, 500],
}
WORKOUT_COUNT_MILESTONES = [10, 25, 50, 100, 200, 300, 500, 1000]
STREAK_MILESTONES_WEEKS = [4, 8, 12, 26, 52]
# A week counts toward a streak with at least this many workouts.
STREAK_MIN_WORKOUTS = 2

FAMILY_LABELS = {"running": "ריצה", "cycling": "רכיבה", "swimming": "שחייה", "strength": "כוח"}


def sport_family(activity_type: Optional[str]) -> Optional[str]:
    for family, types in SPORT_FAMILIES.items():
        if activity_type in types:
            return family
    return None


def _crossed(before: float, after: float, thresholds: list) -> list:
    return [t for t in thresholds if before < t <= after]


def _family_distance_km(session: Session, user_id: int, family: str, exclude_id: Optional[int]) -> float:
    query = select(func.coalesce(func.sum(Activity.distance_meters), 0.0)).where(
        Activity.user_id == user_id,
        Activity.activity_type.in_(SPORT_FAMILIES[family]),
    )
    if exclude_id is not None:
        query = query.where(Activity.id != exclude_id)
    return float(session.exec(query).one() or 0.0) / 1000


def _week_start(day):
    return day - timedelta(days=week_index(day))


def current_streak_weeks(session: Session, user_id: int, through_day) -> tuple[int, Optional[object]]:
    """
    Consecutive weeks (Sunday to Saturday), ending with the week of
    `through_day`, that each had STREAK_MIN_WORKOUTS or more workouts.
    Returns (length, first week of the streak).
    """
    starts = session.exec(select(Activity.start_time).where(Activity.user_id == user_id)).all()
    per_week: dict = {}
    for start in starts:
        week = _week_start(start.date())
        per_week[week] = per_week.get(week, 0) + 1

    week = _week_start(through_day)
    length, first = 0, None
    while per_week.get(week, 0) >= STREAK_MIN_WORKOUTS:
        length += 1
        first = week
        week -= timedelta(days=7)
    return length, first


def _grant(session: Session, user_id: int, key: str, activity_id: int, details: dict) -> bool:
    statement = (
        insert(Achievement)
        .values(user_id=user_id, key=key, activity_id=activity_id, details=details, achieved_at=clock.now_utc())
        .on_conflict_do_nothing(constraint="uq_achievement_user_key")
        .returning(Achievement.id)
    )
    return session.execute(statement).scalar_one_or_none() is not None


def award_for_activity(session: Session, activity: Activity) -> list[dict]:
    """
    Grants whatever this workout earned and returns only what is new, so
    the caller can celebrate it. Safe to call twice: the second call finds
    everything already granted and returns nothing.
    """
    user_id = activity.user_id
    earned: list[dict] = []

    family = sport_family(activity.activity_type)
    if family in DISTANCE_MILESTONES_KM and activity.distance_meters:
        before = _family_distance_km(session, user_id, family, exclude_id=activity.id)
        after = before + activity.distance_meters / 1000
        for km in _crossed(before, after, DISTANCE_MILESTONES_KM[family]):
            if _grant(session, user_id, f"distance:{family}:{km}", activity.id, {"km": km}):
                earned.append({"kind": "distance", "family": family, "km": km})

    if family is not None:
        same_family = session.exec(
            select(func.count()).select_from(Activity).where(
                Activity.user_id == user_id, Activity.activity_type.in_(SPORT_FAMILIES[family])
            )
        ).one()
        if same_family == 1 and _grant(session, user_id, f"first:{family}", activity.id, {}):
            earned.append({"kind": "first", "family": family})

    total = session.exec(
        select(func.count()).select_from(Activity).where(Activity.user_id == user_id)
    ).one()
    for count in _crossed(total - 1, total, WORKOUT_COUNT_MILESTONES):
        if _grant(session, user_id, f"workouts:{count}", activity.id, {"count": count}):
            earned.append({"kind": "workouts", "count": count})

    weeks, first_week = current_streak_weeks(session, user_id, activity.start_time.date())
    if weeks in STREAK_MILESTONES_WEEKS:
        # Keyed by the streak's first week: a streak that breaks and is
        # rebuilt later is a new achievement, the same one re-counted is not.
        if _grant(session, user_id, f"streak:{weeks}:{first_week.isoformat()}", activity.id, {"weeks": weeks}):
            earned.append({"kind": "streak", "weeks": weeks})

    session.commit()
    return earned


def format_achievements(earned: list[dict]) -> str:
    lines = []
    for item in earned:
        if item["kind"] == "distance":
            lines.append(f"📏 עברת *{item['km']:,} ק\"מ* של {FAMILY_LABELS[item['family']]}!")
        elif item["kind"] == "first":
            lines.append(f"🌱 האימון הראשון שלך ב{FAMILY_LABELS[item['family']]} - התחלה מצוינת")
        elif item["kind"] == "workouts":
            lines.append(f"🎯 אימון מספר *{item['count']}*!")
        elif item["kind"] == "streak":
            lines.append(f"🔥 *{item['weeks']} שבועות ברצף* עם {STREAK_MIN_WORKOUTS}+ אימונים בשבוע")
    return "\n".join(lines)
