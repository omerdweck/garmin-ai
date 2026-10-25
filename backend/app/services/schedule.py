"""
The training week, Sunday to Saturday: what was actually done (every
workout that synced or was added by hand), what is planned (saved in the
bot or on the watch), and the old weekly template where one is still active.

Shared by the "🗓 לו״ז אימונים שבועי" screen and the coach's
get_training_schedule tool, so the athlete and the coach always look at the
same week.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import Session, col, select

from app.core import clock
from app.models.activity import Activity
from app.models.plan_session import DAY_NAMES_HE, PlanSession, week_index
from app.models.workout import STATUS_SENT, StoredWorkout
from app.services.achievements import sport_family
from app.services.plan_view import active_plans, discipline_label
from app.services.workouts import scheduled_between

# Garmin activity types for sports sport_family() does not group.
OTHER_FAMILIES = {
    "walking": {"walking", "casual_walking", "speed_walking"},
    "hiking": {"hiking"},
    "yoga": {"yoga"},
    "hiit": {"hiit"},
    "mobility": {"mobility", "stretching"},
    "cardio": {"indoor_cardio", "elliptical", "stair_climbing", "indoor_rowing", "rowing"},
}

PLANNED, DONE, MISSED = "planned", "done", "missed"


def activity_family(activity_type: Optional[str]) -> Optional[str]:
    family = sport_family(activity_type)
    if family:
        return family
    for name, types in OTHER_FAMILIES.items():
        if activity_type in types:
            return name
    return activity_type


def week_start(day: date) -> date:
    """The Sunday that starts `day`'s week."""
    return day - timedelta(days=week_index(day))


@dataclass
class DayPlan:
    day: date
    done: list[Activity] = field(default_factory=list)
    planned: list[tuple[StoredWorkout, str]] = field(default_factory=list)  # (workout, planned/done/missed)
    template: list[tuple[PlanSession, str]] = field(default_factory=list)  # (session, discipline)


def week_schedule(session: Session, user_id: int, week_offset: int = 0) -> list[DayPlan]:
    today = clock.today()
    first = week_start(today) + timedelta(weeks=week_offset)
    last = first + timedelta(days=6)
    days = {first + timedelta(days=i): DayPlan(first + timedelta(days=i)) for i in range(7)}

    activities = session.exec(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.start_time >= datetime.combine(first, datetime.min.time()),
            Activity.start_time < datetime.combine(last + timedelta(days=1), datetime.min.time()),
        )
        .order_by(col(Activity.start_time))
    ).all()
    for activity in activities:
        days[activity.start_time.date()].done.append(activity)

    for workout in scheduled_between(session, user_id, first, last):
        day = days[workout.scheduled_for]
        families_done = {activity_family(a.activity_type) for a in day.done}
        if workout.sport in families_done:
            state = DONE
        elif workout.scheduled_for < today:
            state = MISSED
        else:
            state = PLANNED
        day.planned.append((workout, state))

    plans = {p.id: p.discipline for p in active_plans(session, user_id)}
    if plans:
        for item in session.exec(select(PlanSession).where(col(PlanSession.plan_id).in_(list(plans)))).all():
            for day in days.values():
                if week_index(day.day) == item.day_of_week:
                    day.template.append((item, plans[item.plan_id]))

    return [days[first + timedelta(days=i)] for i in range(7)]


def schedule_as_dict(session: Session, user_id: int, week_offset: int = 0) -> dict:
    """The same week, for the coach."""
    week = week_schedule(session, user_id, week_offset)
    return {
        "week_starts": week[0].day.isoformat(),
        "days": [
            {
                "date": d.day.isoformat(),
                "day": DAY_NAMES_HE[week_index(d.day)],
                "done": [
                    {
                        "type": a.activity_type,
                        "name": a.activity_name,
                        "minutes": round((a.duration_seconds or 0) / 60),
                        "km": round((a.distance_meters or 0) / 1000, 2) or None,
                    }
                    for a in d.done
                ],
                "planned": [
                    {
                        "workout_id": w.id,
                        "sport": w.sport,
                        "name": w.name,
                        "on_watch": w.status == STATUS_SENT,
                        "state": state,
                    }
                    for w, state in d.planned
                ],
                "weekly_template": [
                    {"discipline": discipline_label(disc), "title": s.title, "is_rest": s.is_rest}
                    for s, disc in d.template
                ],
            }
            for d in week
        ],
    }
