"""
Storing coach-built workouts, and the three things that touch Garmin:
send to the watch (upload + put on the calendar), .fit file, remove.

The Garmin calls run in the worker (tasks.py), never in the bot. Every one
checks that the workout belongs to the user asking, and every upload is
gated by `can_send_to_watch` - see WORKOUT_UPLOAD_SPORTS in config.py.
"""

import logging
import secrets
from datetime import date
from typing import Optional

from sqlmodel import Session, col, select

from app.core import clock
from app.core.config import settings
from app.core.crypto import decrypt
from app.core.garmin_client import (
    delete_workout,
    download_workout_fit,
    resume_garmin_session,
    schedule_workout,
    unschedule_workout,
    upload_workout,
)
from app.models.athlete_profile import AthleteProfile
from app.models.garmin_account import GarminAccount
from app.models.user import User
from app.models.workout import STATUS_DRAFT, STATUS_PLANNED, STATUS_REMOVED, STATUS_SENT, StoredWorkout
from app.services.workout_garmin import to_garmin
from app.services.workout_schema import Workout, parse_workout

logger = logging.getLogger(__name__)


class WorkoutNotFound(LookupError):
    pass


def save_workout(
    session: Session, user_id: int, workout: Workout, source: str = "coach", replaces: Optional[int] = None
) -> StoredWorkout:
    """
    A new draft. `replaces` names one of this user's workouts it is a new
    version of; the old one is only removed once this one is kept
    (see take_over_from_old).
    """
    old = session.get(StoredWorkout, replaces) if replaces else None
    if old is not None and (old.user_id != user_id or old.status == STATUS_REMOVED):
        old = None
    if old is not None and workout.date is None and old.scheduled_for:
        workout = workout.model_copy(update={"date": old.scheduled_for})
    row = StoredWorkout(
        user_id=user_id,
        sport=workout.sport,
        name=workout.name,
        spec=workout.model_dump(mode="json", exclude_none=True),
        source=source,
        scheduled_for=workout.date,
        replaces_workout_id=old.id if old else None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def load_workout(session: Session, user_id: int, workout_id: int) -> tuple[StoredWorkout, Workout]:
    """The stored row and the parsed workout - only if it is this user's."""
    row = session.get(StoredWorkout, workout_id)
    if row is None or row.user_id != user_id or row.status == STATUS_REMOVED:
        raise WorkoutNotFound(workout_id)
    workout, errors = parse_workout(row.spec)
    if workout is None:  # stored before a rule tightened - never send it
        raise WorkoutNotFound(f"{workout_id}: {errors}")
    return row, workout


def user_ftp(session: Session, user_id: int) -> Optional[int]:
    profile = session.exec(select(AthleteProfile).where(AthleteProfile.user_id == user_id)).first()
    return profile.ftp_watts if profile else None


def can_send_to_watch(user: User, sport: str) -> bool:
    if settings.admin_chat_id is not None and user.telegram_chat_id == settings.admin_chat_id:
        return True
    return sport in settings.workout_upload_sport_set


def _garmin(session: Session, user_id: int):
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user_id)).first()
    if account is None or account.disconnected_at is not None:
        raise WorkoutNotFound("no connected Garmin account")
    return resume_garmin_session(decrypt(account.encrypted_token))


def send_to_watch(session: Session, user: User, workout_id: int, day: date) -> StoredWorkout:
    """
    Uploads it (once) and puts it on the Garmin calendar for `day`. Sending
    the same workout again for another day reuses the uploaded copy rather
    than filling the athlete's library with duplicates.
    """
    row, workout = load_workout(session, user.id, workout_id)
    if not can_send_to_watch(user, workout.sport):
        raise PermissionError(workout.sport)

    garmin = _garmin(session, user.id)
    if row.garmin_workout_id is None:
        payload = to_garmin(workout, user_ftp(session, user.id))
        # Stored before the request: if the upload half-succeeds, what we
        # tried to send is still on record.
        row.sent_json = payload
        session.add(row)
        session.commit()
        row.garmin_workout_id, _ = upload_workout(garmin, payload)
        logger.info("Workout %s uploaded for user %s as Garmin %s", row.id, user.id, row.garmin_workout_id)

    if row.garmin_schedule_id is not None and row.scheduled_for != day:
        # Moved to another day: one calendar entry, not two.
        unschedule_workout(garmin, row.garmin_schedule_id)
    elif row.garmin_schedule_id is not None:
        return row  # already on that day - pressing twice changes nothing

    row.spec = {**row.spec, "date": day.isoformat()}
    row.garmin_schedule_id = schedule_workout(garmin, row.garmin_workout_id, day.isoformat())
    row.scheduled_for = day
    row.status = STATUS_SENT
    row.sent_at = clock.now_utc()
    session.add(row)
    session.commit()
    return row


def fit_file(session: Session, user: User, workout_id: int) -> tuple[str, bytes]:
    """
    The .fit file, produced by Garmin from the uploaded workout - the one
    format the watch is guaranteed to read. A workout that was never sent is
    uploaded for the moment and deleted again, so asking for a file leaves
    nothing behind in the athlete's library.
    """
    row, workout = load_workout(session, user.id, workout_id)
    if not can_send_to_watch(user, workout.sport):
        raise PermissionError(workout.sport)
    garmin = _garmin(session, user.id)
    if row.garmin_workout_id is not None:
        return f"{_safe_name(row.name)}.fit", download_workout_fit(garmin, row.garmin_workout_id)

    temporary_id, _ = upload_workout(garmin, to_garmin(workout, user_ftp(session, user.id)))
    try:
        content = download_workout_fit(garmin, temporary_id)
    finally:
        delete_workout(garmin, temporary_id)
    return f"{_safe_name(row.name)}.fit", content


def remove_from_watch(session: Session, user: User, workout_id: int) -> None:
    row, _ = load_workout(session, user.id, workout_id)
    if row.garmin_workout_id is not None:
        delete_workout(_garmin(session, user.id), row.garmin_workout_id)
    row.status = STATUS_REMOVED
    row.garmin_workout_id = None
    row.garmin_schedule_id = None
    session.add(row)
    session.commit()


def plan_workout(session: Session, user_id: int, workout_id: int, day: date) -> StoredWorkout:
    """
    Saves it in the bot for `day` - the athlete's week, the morning reminder -
    without touching Garmin. A workout already on the watch is moved through
    send_to_watch instead, so the bot and the watch never disagree on the day.
    """
    row, _ = load_workout(session, user_id, workout_id)
    if row.status == STATUS_SENT:
        raise ValueError("already on the watch - move it with send_to_watch")
    row.scheduled_for = day
    row.status = STATUS_PLANNED
    row.spec = {**row.spec, "date": day.isoformat()}
    session.add(row)
    session.commit()
    return row


def take_over_from_old(session: Session, user_id: int, row: StoredWorkout) -> Optional[int]:
    """
    Called when a new version is saved or sent: the version it replaces
    goes. Returns that old workout's id when it is on the watch - the caller
    queues its removal from Garmin; anything else is removed right here.
    """
    if not row.replaces_workout_id:
        return None
    old = session.get(StoredWorkout, row.replaces_workout_id)
    row.replaces_workout_id = None
    session.add(row)
    session.commit()
    if old is None or old.user_id != user_id or old.status == STATUS_REMOVED:
        return None
    if old.status == STATUS_SENT:
        return old.id
    old.status = STATUS_REMOVED
    session.add(old)
    session.commit()
    return None


def discard_draft(session: Session, user_id: int, workout_id: int) -> bool:
    """Removes a workout that is not on the watch (a draft, or one saved for a day)."""
    row = session.get(StoredWorkout, workout_id)
    if row is None or row.user_id != user_id or row.status not in (STATUS_DRAFT, STATUS_PLANNED):
        return False
    row.status = STATUS_REMOVED
    session.add(row)
    session.commit()
    return True


def _safe_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
    return cleaned[:40] or "workout"


def file_name(row: StoredWorkout, extension: str) -> str:
    return f"{_safe_name(row.name)}.{extension}"


def scheduled_between(session: Session, user_id: int, first: date, last: date) -> list[StoredWorkout]:
    """Workouts with a day in [first, last] - saved in the bot or on the watch."""
    return session.exec(
        select(StoredWorkout)
        .where(
            StoredWorkout.user_id == user_id,
            col(StoredWorkout.status).in_((STATUS_PLANNED, STATUS_SENT)),
            StoredWorkout.scheduled_for >= first,
            StoredWorkout.scheduled_for <= last,
        )
        .order_by(col(StoredWorkout.scheduled_for), col(StoredWorkout.id))
    ).all()



def save_week(session: Session, user_id: int, workouts: list[Workout]) -> tuple[str, list[StoredWorkout]]:
    """
    A week the coach proposed, saved as drafts that share one proposal id.
    Any earlier proposal still waiting for review is withdrawn: the athlete
    asked for changes, and two versions of the same week side by side would
    leave them choosing between the old and the new one workout by workout.
    """
    for old in session.exec(
        select(StoredWorkout).where(
            StoredWorkout.user_id == user_id,
            StoredWorkout.status == STATUS_DRAFT,
            StoredWorkout.proposal_id.is_not(None),
        )
    ).all():
        old.status = STATUS_REMOVED
        session.add(old)

    proposal_id = secrets.token_hex(6)
    rows = []
    for workout in sorted(workouts, key=lambda w: w.date):
        row = StoredWorkout(
            user_id=user_id,
            sport=workout.sport,
            name=workout.name,
            spec=workout.model_dump(mode="json", exclude_none=True),
            scheduled_for=workout.date,
            proposal_id=proposal_id,
        )
        session.add(row)
        rows.append(row)
    session.commit()
    for row in rows:
        session.refresh(row)
    return proposal_id, rows


def proposal_rows(
    session: Session, user_id: int, proposal_id: str, include_removed: bool = False
) -> list[StoredWorkout]:
    """
    The workouts of one proposed week, in date order. The review includes
    the skipped ones, so "workout 3 of 5" keeps meaning the same workout.
    """
    query = select(StoredWorkout).where(
        StoredWorkout.user_id == user_id, StoredWorkout.proposal_id == proposal_id
    )
    if not include_removed:
        query = query.where(StoredWorkout.status != STATUS_REMOVED)
    return session.exec(query.order_by(col(StoredWorkout.scheduled_for), col(StoredWorkout.id))).all()


def latest_proposal_since(session: Session, user_id: int, since) -> Optional[str]:
    row = session.exec(
        select(StoredWorkout)
        .where(
            StoredWorkout.user_id == user_id,
            StoredWorkout.proposal_id.is_not(None),
            StoredWorkout.status == STATUS_DRAFT,
            StoredWorkout.created_at >= since,
        )
        .order_by(col(StoredWorkout.id).desc())
    ).first()
    return row.proposal_id if row else None
