"""
The message after a workout.

Three parts, each from the cheapest source that can produce it:
  1. the workout's numbers      - format_activity_detail, Python, exact
  2. a short coaching paragraph - Claude, given facts and told not to repeat them
  3. rewards and new records    - Python templates (achievements.py, records.py)

If Claude is unavailable, parts 1 and 3 still go out: a finished workout
deserves an answer even when the AI does not have one.
"""

import logging
from datetime import timedelta
from typing import Optional

from sqlmodel import Session, col, select

from app.core import clock
from app.core.claude_client import (
    ClaudeAuthError,
    ClaudeNotConfiguredError,
    ClaudeOutOfCreditError,
    ClaudeUnavailableError,
    _activity_to_dict,
    fetch_training_plans,
    generate_post_workout_note,
)
from app.models.activity import SOURCE_GARMIN, Activity
from app.models.coach_event import KIND_POST_WORKOUT, CoachEvent
from app.models.plan_session import DAY_KEYS, week_index
from app.models.user import User
from app.services.achievements import award_for_activity, format_achievements, sport_family
from app.services.activity_view import format_activity_detail
from app.services.coach_events import record_event, register_composer
from app.services.records import format_broken_records

logger = logging.getLogger(__name__)

# Only workouts this recent get a message. A sync can bring in older ones -
# the first sync after linking pulls the last twenty - and "great run!" about
# a run from two weeks ago is noise, not coaching.
FRESH_FOR = timedelta(hours=36)

# Plan disciplines, matched to the sport family of the workout.
FAMILY_TO_DISCIPLINE = {"running": "running", "cycling": "cycling", "swimming": "swimming", "strength": "strength"}

# Extra Garmin fields worth giving the coach about one workout. Already in
# raw_json; the base projection leaves them out to keep list views small.
EXTRA_FIELDS = {
    "elevationGain": "elevation_gain_m",
    "averageRunningCadenceInStepsPerMinute": "cadence_spm",
    "aerobicTrainingEffect": "aerobic_training_effect",
    "anaerobicTrainingEffect": "anaerobic_training_effect",
    "trainingEffectLabel": "training_effect_label",
    "activityTrainingLoad": "training_load",
    "avgPower": "avg_power_w",
    "averageSwolf": "avg_swolf",
    "totalSets": "total_sets",
    "totalReps": "total_reps",
}


def latest_garmin_activity_id(session: Session, user_id: int) -> Optional[int]:
    """Taken before a sync; everything with a higher id afterwards is new."""
    return session.exec(
        select(Activity.id)
        .where(Activity.user_id == user_id, Activity.source == SOURCE_GARMIN)
        .order_by(col(Activity.id).desc())
    ).first()


def fresh_activities_since(session: Session, user_id: int, after_id: Optional[int]) -> list[Activity]:
    cutoff = clock.now_local_naive() - FRESH_FOR
    query = select(Activity).where(
        Activity.user_id == user_id,
        Activity.source == SOURCE_GARMIN,
        Activity.start_time >= cutoff,
    )
    if after_id is not None:
        query = query.where(Activity.id > after_id)
    return session.exec(query.order_by(col(Activity.start_time))).all()


def record_post_workout_events(
    session: Session, user: User, activities: list[Activity], broken_records: list
) -> list[CoachEvent]:
    """
    One event per workout. New personal records ride on the latest one,
    since that is almost always the workout that set them - and the caller
    then skips the separate record message.
    """
    events = []
    for index, activity in enumerate(activities):
        # Awarded here, at sync time, and carried in the payload - not in the
        # composer. A delivery that is retried would otherwise find the
        # rewards already granted by the first attempt and leave them out.
        payload = {"activity_id": activity.id, "achievements": award_for_activity(session, activity)}
        if broken_records and index == len(activities) - 1:
            payload["records"] = broken_records
        event = record_event(
            session, user.id, KIND_POST_WORKOUT, f"post_workout:{activity.garmin_activity_id}", payload
        )
        if event is not None:
            events.append(event)
    return events


def _recent_similar(session: Session, activity: Activity, limit: int = 5) -> list[dict]:
    rows = session.exec(
        select(Activity)
        .where(
            Activity.user_id == activity.user_id,
            Activity.activity_type == activity.activity_type,
            Activity.id != activity.id,
            Activity.start_time < activity.start_time,
        )
        .order_by(col(Activity.start_time).desc())
        .limit(limit)
    ).all()
    return [_activity_to_dict(row) for row in rows]


def _planned_today(session: Session, activity: Activity) -> list[dict]:
    discipline = FAMILY_TO_DISCIPLINE.get(sport_family(activity.activity_type) or "")
    day_key = DAY_KEYS[week_index(activity.start_time.date())]
    planned = []
    for plan in fetch_training_plans(session, activity.user_id):
        if plan["status"] != "active":
            continue
        if discipline and plan["discipline"] not in (discipline, "general"):
            continue
        planned += [item for item in plan["week"] if item["day"] == day_key]
    return planned


def workout_facts(session: Session, activity: Activity, earned: list[dict]) -> dict:
    facts = _activity_to_dict(activity)
    raw = activity.raw_json or {}
    for garmin_key, name in EXTRA_FIELDS.items():
        if raw.get(garmin_key) is not None:
            facts[name] = raw[garmin_key]
    return {
        "workout": facts,
        "recent_similar": _recent_similar(session, activity),
        "planned_today": _planned_today(session, activity),
        "achievements": earned,
    }


@register_composer(KIND_POST_WORKOUT, uses_ai=True)
def compose_post_workout(session: Session, user: User, event: CoachEvent) -> Optional[str]:
    activity = session.get(Activity, (event.payload or {}).get("activity_id"))
    if activity is None or activity.user_id != user.id:
        return None  # deleted since, or not theirs - nothing to say

    earned = (event.payload or {}).get("achievements") or []
    parts = [format_activity_detail(activity)]

    try:
        note = generate_post_workout_note(session, user, workout_facts(session, activity, earned))
    except (ClaudeNotConfiguredError, ClaudeAuthError, ClaudeOutOfCreditError, ClaudeUnavailableError) as exc:
        logger.warning("Post-workout note without AI for user %s - %s", user.id, type(exc).__name__)
        note = None
    if note:
        parts.append(note)

    rewards = format_achievements(earned)
    if rewards:
        parts.append(rewards)
    records = (event.payload or {}).get("records")
    if records:
        parts.append(format_broken_records(records))

    return "\n\n".join(parts)
