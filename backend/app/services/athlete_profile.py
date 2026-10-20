"""
Reading and writing the coach's long-term memory of an athlete.

Shared by the coach's tools, the /profile screen and the prompt builder, so
all three see the same thing - a profile the coach describes differently
from the one the user can read would be worse than none.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, col, delete, select

from app.models.athlete_profile import (
    EXPERIENCE_LEVELS,
    MAX_NOTE_LENGTH,
    MAX_NOTES,
    SPORTS,
    AthleteProfile,
    CoachNote,
)
from app.models.plan_session import DAY_KEYS

# Field -> how to validate a value the model sends. Anything outside these
# is dropped rather than stored: the profile feeds plan building, and a
# training day of "funday" or a pool of -25m would quietly poison it.
PROFILE_FIELDS = {
    "sports": lambda v: [s for s in v if s in SPORTS] if isinstance(v, list) else None,
    "experience": lambda v: v if v in EXPERIENCE_LEVELS else None,
    "training_days": lambda v: [d for d in v if d in DAY_KEYS] if isinstance(v, list) else None,
    "max_session_minutes": lambda v: v if isinstance(v, int) and 5 <= v <= 600 else None,
    "equipment": lambda v: v.strip()[:300] if isinstance(v, str) and v.strip() else None,
    "pool_length_m": lambda v: v if isinstance(v, int) and 10 <= v <= 100 else None,
    "ftp_watts": lambda v: v if isinstance(v, int) and 50 <= v <= 600 else None,
    "limitations": lambda v: v.strip()[:500] if isinstance(v, str) and v.strip() else None,
}


def get_profile(session: Session, user_id: int) -> Optional[AthleteProfile]:
    return session.exec(select(AthleteProfile).where(AthleteProfile.user_id == user_id)).first()


def get_notes(session: Session, user_id: int) -> list[CoachNote]:
    return session.exec(
        select(CoachNote).where(CoachNote.user_id == user_id).order_by(col(CoachNote.created_at))
    ).all()


def update_profile(session: Session, user_id: int, fields: dict) -> tuple[dict, list[str]]:
    """Returns (what was saved, names of fields that were rejected)."""
    profile = get_profile(session, user_id) or AthleteProfile(user_id=user_id)
    saved, rejected = {}, []
    for name, raw in fields.items():
        validate = PROFILE_FIELDS.get(name)
        value = validate(raw) if validate else None
        if value in (None, []):
            rejected.append(name)
            continue
        setattr(profile, name, value)
        saved[name] = value
    if saved:
        profile.updated_at = datetime.now(timezone.utc)
        session.add(profile)
        session.commit()
    return saved, rejected


def add_note(session: Session, user_id: int, text: str) -> tuple[bool, str]:
    text = (text or "").strip()
    if not text:
        return False, "empty"
    if len(text) > MAX_NOTE_LENGTH:
        return False, "too_long"

    notes = get_notes(session, user_id)
    if any(n.text == text for n in notes):
        return False, "duplicate"
    if len(notes) >= MAX_NOTES:
        # Not silently evicting the oldest: which facts still matter is the
        # user's call, and they can prune from /profile.
        return False, "full"

    session.add(CoachNote(user_id=user_id, text=text))
    session.commit()
    return True, "saved"


def delete_note(session: Session, user_id: int, note_id: int) -> bool:
    """Scoped to the user: the id arrives from a button, which a client can forge."""
    result = session.exec(
        delete(CoachNote).where(CoachNote.id == note_id, CoachNote.user_id == user_id)
    )
    session.commit()
    return result.rowcount > 0


def clear_notes(session: Session, user_id: int) -> int:
    result = session.exec(delete(CoachNote).where(CoachNote.user_id == user_id))
    session.commit()
    return result.rowcount


def profile_as_dict(session: Session, user_id: int) -> dict:
    profile = get_profile(session, user_id)
    fields = {}
    if profile is not None:
        fields = {
            name: getattr(profile, name)
            for name in PROFILE_FIELDS
            if getattr(profile, name) not in (None, [])
        }
    return {
        "profile": fields,
        "notes": [
            {"id": n.id, "text": n.text, "saved_on": n.created_at.date().isoformat()}
            for n in get_notes(session, user_id)
        ],
    }


def profile_prompt_block(session: Session, user_id: int) -> Optional[str]:
    """
    The compact version that goes into every coach request, or None when
    there is nothing to say. Kept out of the cached system prompt on purpose
    - see chat_with_coach.
    """
    data = profile_as_dict(session, user_id)
    if not data["profile"] and not data["notes"]:
        return None

    lines = ["What this athlete has told you (their own words; trust it, do not re-ask):"]
    for name, value in data["profile"].items():
        shown = ", ".join(value) if isinstance(value, list) else value
        lines.append(f"- {name}: {shown}")
    if data["notes"]:
        lines.append("Things you chose to remember:")
        lines.extend(f"- ({n['saved_on']}) {n['text']}" for n in data["notes"])
    return "\n".join(lines)
