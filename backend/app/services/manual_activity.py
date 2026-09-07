"""
Manually entered workouts - the ones done without the watch.

Written into the same `activity` table as everything Garmin sends, because
every consumer downstream (the coach's tools, the workout browser, the week
rollup, plan adherence) already reads that table. A second table would mean
touching all of them and turning "my workouts" into two lists to reconcile.

What a manual row does not have is most of the columns: no heart rate, no
calories, no steps, no per-set breakdown. Those stay NULL rather than being
filled with a guess. The display layer already skips absent fields, and the
coach's projection already strips nulls, so absence reads as absence.
"""

import logging
import re
from datetime import datetime, time, timedelta
from typing import Optional

from sqlmodel import Session, col, select

from app.models.activity import SOURCE_MANUAL, Activity

logger = logging.getLogger(__name__)

# Keys are Garmin's own activityType.typeKey values, deliberately: reusing
# them means the existing labels, per-type formatters and muscle-group
# mapping all work on a manual row with no special casing anywhere.
MANUAL_TYPES = [
    ("running", "🏃 ריצה"),
    ("lap_swimming", "🏊 שחייה"),
    ("cycling", "🚴 אופניים"),
    ("strength_training", "🏋️ אימון כוח"),
    ("walking", "🚶 הליכה"),
    ("cardio", "💪 אחר"),
]

# Types where a distance is meaningful. Asking a strength session for its
# distance is a question with no answer, and an extra step people would have
# to skip every time.
DISTANCE_TYPES = {"running", "lap_swimming", "cycling", "walking"}

DURATION_CHOICES = [20, 30, 45, 60, 90]

# Bounds for typed input. Generous rather than tight - the point is to catch
# a typo or a stray word, not to argue with someone who ran an ultra.
MIN_DURATION_MINUTES = 1
MAX_DURATION_MINUTES = 24 * 60
MAX_DISTANCE_KM = 300
MAX_SWIM_DISTANCE_M = 50_000
DISTANCE_CHOICES_KM = [3, 5, 7, 10]
# Swimming is measured in metres and at a completely different scale - 5 km
# is a serious swim, so offering the running ladder would be useless.
SWIM_CHOICES_M = [500, 1000, 1500, 2000]


def parse_duration(text: str) -> Optional[int]:
    """
    Minutes from whatever the user typed, or None if it is not a usable
    number. Accepts "45", "45 דקות", "1:30" - people write a duration the
    way they say it, and rejecting "45 דקות" because of the word would be
    the kind of pedantry that makes a form feel broken.
    """
    text = (text or "").strip().replace(",", ".")

    # "1:30" means an hour and a half, not one point three.
    match = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if match:
        minutes = int(match.group(1)) * 60 + int(match.group(2))
        return minutes if MIN_DURATION_MINUTES <= minutes <= MAX_DURATION_MINUTES else None

    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers:
        return None
    minutes = int(round(float(numbers[0])))
    return minutes if MIN_DURATION_MINUTES <= minutes <= MAX_DURATION_MINUTES else None


def parse_distance(text: str, is_swim: bool) -> Optional[float]:
    """
    Distance in metres, reading the unit the user wrote rather than assuming
    one from the sport.

    This is the whole point: "800" in a run means 800 km if you assume
    kilometres, and someone typing "800 מטר" plainly means 800 metres.
    Getting it wrong stores a workout off by a factor of a thousand, and
    nothing downstream would question an 800 km run.

    Only when no unit is written does the sport decide - kilometres for
    running, cycling and walking, metres for swimming, which is how each is
    actually talked about.
    """
    text = (text or "").strip().replace(",", ".").replace("״", '"').replace("’", "'")
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers:
        return None
    value = float(numbers[0])

    lowered = text.lower()
    # Kilometres checked first: every Hebrew spelling of "ק\"מ" contains the
    # letter מ, so testing for metres first would match all of them.
    if re.search(r'ק"?מ|קילומטר|\bkm\b', lowered):
        unit = "km"
    elif re.search(r"מטר|מ'|\bm\b|\bmeters?\b", lowered):
        unit = "m"
    else:
        unit = "m" if is_swim else "km"

    meters = value if unit == "m" else value * 1000
    if meters <= 0:
        return None
    limit = MAX_SWIM_DISTANCE_M if is_swim else MAX_DISTANCE_KM * 1000
    return meters if meters <= limit else None


def type_label(type_key: str) -> str:
    return dict(MANUAL_TYPES).get(type_key, type_key)


def takes_distance(type_key: str) -> bool:
    return type_key in DISTANCE_TYPES


def build_start_time(day_offset: int) -> datetime:
    """
    A timestamp for a workout the user is describing after the fact.

    Today gets the current time; an earlier day gets midday. Neither is the
    real start time and neither can be - the point is only to order the
    workout correctly within its day and to land it on the right date.
    Asking for a time would be a whole extra step to buy precision nothing
    here uses.
    """
    day = datetime.now() - timedelta(days=day_offset)
    if day_offset == 0:
        return day
    return datetime.combine(day.date(), time(12, 0))


def create_manual_activity(
    session: Session,
    user_id: int,
    activity_type: str,
    start_time: datetime,
    duration_minutes: int,
    distance_meters: Optional[float] = None,
) -> Activity:
    activity = Activity(
        user_id=user_id,
        garmin_activity_id=None,
        source=SOURCE_MANUAL,
        activity_type=activity_type,
        activity_name=type_label(activity_type).split(" ", 1)[-1],
        start_time=start_time,
        duration_seconds=duration_minutes * 60,
        distance_meters=distance_meters,
        # Everything else stays NULL on purpose - see the module docstring.
    )
    session.add(activity)
    session.commit()
    session.refresh(activity)
    logger.info("Manual activity %s created for user %s", activity.id, user_id)
    return activity


def recent_manual(session: Session, user_id: int, limit: int = 10) -> list[Activity]:
    return list(
        session.exec(
            select(Activity)
            .where(Activity.user_id == user_id, Activity.source == SOURCE_MANUAL)
            .order_by(col(Activity.start_time).desc())
            .limit(limit)
        ).all()
    )


def delete_manual_activity(session: Session, user_id: int, activity_id: int) -> bool:
    """
    Only manual rows can be deleted, and only the owner's.

    Scoped by user_id because the id arrives from a callback the client
    sends, and restricted to manual rows because deleting a Garmin one
    would accomplish nothing - the next sync would put it straight back.
    """
    activity = session.exec(
        select(Activity).where(
            Activity.id == activity_id,
            Activity.user_id == user_id,
            Activity.source == SOURCE_MANUAL,
        )
    ).first()
    if activity is None:
        return False

    session.delete(activity)
    session.commit()
    return True
