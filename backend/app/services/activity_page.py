"""
The Mini App's workout list and single-workout page. Read-only, like the
other dashboard modules - with one exception: a workout opened without its
detail yet asks the worker to fetch it (a message on the Redis queue, never
a database write), and the page polls until it arrives.
"""

import logging
from typing import Optional

import redis
from sqlmodel import Session, col, select

from app.core.config import settings
from app.models.activity import SOURCE_MANUAL, Activity
from app.services.activity_view import EXERCISE_MUSCLE_GROUPS
from app.services.workout_stats import dashboard_sport

logger = logging.getLogger(__name__)

# Same key and lifetime as app/tasks.py (not imported from there: the
# webapp process shouldn't load every task's dependencies to queue one).
DETAIL_PENDING_KEY = "activity-detail-pending:{}"
DETAIL_PENDING_SECONDS = 180
THROTTLE_KEY = "garmin-throttled"

# Extra figures Garmin already sent with the workout (raw_json), by what the page calls them.
RAW_FIELDS = {
    "elevationGain": "elevation_gain",
    "elevationLoss": "elevation_loss",
    "movingDuration": "moving_seconds",
    "averageSpeed": "avg_speed",
    "maxSpeed": "max_speed",
    "averageRunningCadenceInStepsPerMinute": "cadence",
    "averageBikingCadenceInRevPerMinute": "bike_cadence",
    "avgPower": "avg_power",
    "normPower": "norm_power",
    "aerobicTrainingEffect": "aerobic_te",
    "anaerobicTrainingEffect": "anaerobic_te",
    "trainingEffectLabel": "te_label",
    "activityTrainingLoad": "load",
    "averageSwolf": "avg_swolf",
    "poolLength": "pool_length",
    "totalSets": "sets",
    "totalReps": "reps",
    "avgStrideLength": "stride_cm",
}


def _row(a: Activity) -> dict:
    return {
        "id": a.id,
        "name": a.activity_name or "",
        "sport": dashboard_sport(a.activity_type),
        "type": a.activity_type,
        "start": a.start_time.isoformat(),
        "seconds": round(a.duration_seconds or 0),
        "meters": round(a.distance_meters or 0),
        "avg_hr": round(a.avg_heart_rate) if a.avg_heart_rate else None,
        "manual": a.source == SOURCE_MANUAL,
    }


def recent(session: Session, user_id: int, before: Optional[int], limit: int) -> dict:
    query = select(Activity).where(Activity.user_id == user_id)
    if before:
        cutoff = session.exec(select(Activity.start_time).where(Activity.id == before, Activity.user_id == user_id)).first()
        if cutoff:
            query = query.where(Activity.start_time < cutoff)
    rows = session.exec(query.order_by(col(Activity.start_time).desc()).limit(limit + 1)).all()
    return {"activities": [_row(a) for a in rows[:limit]], "more": len(rows) > limit}


def _sets(activity: Activity) -> list[dict]:
    """Strength: each working set with its exercise, reps and weight."""
    out = []
    for entry in (activity.exercise_sets or {}).get("exerciseSets") or []:
        if entry.get("setType") != "ACTIVE":
            continue
        candidates = [e for e in entry.get("exercises") or [] if e.get("category") not in (None, "UNKNOWN")]
        best = max(candidates, key=lambda e: e.get("probability") or 0) if candidates else {}
        category = best.get("category")
        weight = entry.get("weight")
        out.append({
            "exercise": (category or "").replace("_", " ").title() or None,
            "muscle": EXERCISE_MUSCLE_GROUPS.get(category),
            "reps": entry.get("repetitionCount"),
            # Garmin sends grams.
            "kg": round(weight / 1000, 1) if isinstance(weight, (int, float)) and weight > 0 else None,
            "seconds": round(entry.get("duration") or 0),
        })
    return out


def page(session: Session, user_id: int, activity_id: int) -> Optional[dict]:
    activity = session.exec(
        select(Activity).where(Activity.id == activity_id, Activity.user_id == user_id)
    ).first()
    if activity is None:
        return None
    raw = activity.raw_json or {}
    if activity.source == SOURCE_MANUAL or activity.garmin_activity_id is None:
        status = "manual"
    elif activity.detail is not None:
        status = "ready"
    else:
        status = "missing"
    return {
        **_row(activity),
        "calories": round(activity.calories) if activity.calories else None,
        "max_hr": round(activity.max_heart_rate) if activity.max_heart_rate else None,
        "extra": {name: raw.get(key) for key, name in RAW_FIELDS.items() if raw.get(key) is not None},
        "sets": _sets(activity),
        "detail": activity.detail,
        "status": status,
    }


def _redis() -> redis.Redis:
    return redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0, socket_connect_timeout=2)


def request_detail(activity_id: int) -> str:
    """
    Queues the fetch once (SET NX: a second open while it's pending queues
    nothing). Returns the page status: "pending", or "busy" while Garmin is
    throttling us - then the page says so instead of spinning.
    """
    try:
        client = _redis()
        if client.exists(THROTTLE_KEY):
            return "busy"
        if client.set(DETAIL_PENDING_KEY.format(activity_id), "1", nx=True, ex=DETAIL_PENDING_SECONDS):
            from app.celery_app import celery_app

            celery_app.send_task("app.tasks.fetch_activity_detail_task", args=[activity_id])
        return "pending"
    except Exception:
        logger.warning("Could not queue detail fetch for activity %s", activity_id, exc_info=True)
        return "busy"
