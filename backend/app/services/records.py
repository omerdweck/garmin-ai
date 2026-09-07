"""
Personal records: which of Garmin's to trust, and noticing a new one.

Garmin returns records as numeric typeIds with no label - every
`prTypeLabelKey` came back null for the account this was built against, so
the meaning of each id has to be decided here.

Rather than guess, each id below was verified by fetching the activity the
record points at and checking the numbers line up: typeId 3's value of
1310.3 seconds against a 5,014 m run lasting 1,310 seconds is a best 5 km
and nothing else; typeId 7's 10,001.6 against a 10,002 m run is a distance.

Ids that could not be verified are deliberately absent. Five of them carry
no activity type and no linked activity at all, and two swim records match
no plausible reading of the linked swim. A record shown under the wrong
name is worse than one not shown: the user has no way to know it is wrong,
and it is the kind of confidently-wrong output the rest of this project is
built to avoid.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models.personal_record import PersonalRecord

logger = logging.getLogger(__name__)

# garmin typeId -> (our key, sport, Hebrew label, unit, lower_is_better)
RECORD_TYPES = {
    1: ("run_1k", "running", "הקילומטר המהיר ביותר", "time", True),
    2: ("run_mile", "running", "המייל המהיר ביותר", "time", True),
    3: ("run_5k", "running", "5 ק\"מ המהיר ביותר", "time", True),
    4: ("run_10k", "running", "10 ק\"מ המהיר ביותר", "time", True),
    7: ("run_longest", "running", "הריצה הארוכה ביותר", "distance", False),
    17: ("swim_longest", "swimming", "השחייה הארוכה ביותר", "distance", False),
}

SPORT_LABELS = {"running": "🏃 ריצה", "swimming": "🏊 שחייה"}

RLM = "‏"


def _rtl(text: str) -> str:
    return f"{RLM}{text}"


def record_meta(record_type: str) -> Optional[tuple]:
    for _, meta in RECORD_TYPES.items():
        if meta[0] == record_type:
            return meta
    return None


def format_value(record_type: str, value: float) -> str:
    meta = record_meta(record_type)
    if meta is None:
        return str(value)

    if meta[3] == "distance":
        km = value / 1000
        return f"{km:.2f} ק\"מ" if km >= 1 else f"{value:.0f} מטר"

    seconds = int(round(value))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _apply_records(session: Session, user_id: int, raw_records: list) -> list[dict]:
    """Compare and stage the writes. Caller commits - see sync_personal_records."""
    improved: list[dict] = []

    for raw in raw_records or []:
        meta = RECORD_TYPES.get(raw.get("typeId"))
        if meta is None:
            continue
        key, sport, label, unit, lower_is_better = meta

        value = raw.get("value")
        if value is None:
            continue

        existing = session.exec(
            select(PersonalRecord).where(
                PersonalRecord.user_id == user_id,
                PersonalRecord.record_type == key,
            )
        ).first()

        achieved = None
        stamp = raw.get("prStartTimeLocalFormatted") or raw.get("activityStartDateTimeLocalFormatted")
        if stamp:
            try:
                achieved = datetime.fromisoformat(stamp.replace("Z", "")).date()
            except ValueError:
                achieved = None

        if existing is None:
            session.add(
                PersonalRecord(
                    user_id=user_id,
                    record_type=key,
                    value=float(value),
                    achieved_at=achieved,
                    garmin_activity_id=raw.get("activityId"),
                )
            )
            continue

        better = value < existing.value if lower_is_better else value > existing.value
        if better:
            improved.append(
                {
                    "record_type": key,
                    "sport": sport,
                    "label": label,
                    "previous": existing.value,
                    "value": float(value),
                    "achieved_at": achieved,
                }
            )
            existing.value = float(value)
            existing.achieved_at = achieved
            existing.garmin_activity_id = raw.get("activityId")
            existing.updated_at = datetime.now(timezone.utc)
            session.add(existing)

    return improved


def sync_personal_records(session: Session, user_id: int, raw_records: list) -> list[dict]:
    """
    Mirrors Garmin's records and returns the ones that just improved.

    Improvement has a direction per record: a faster 5 km is a smaller
    number, a longer run is a bigger one. Comparing without that would
    congratulate someone for a slower time.

    A record seen for the first time is stored but NOT reported as broken.
    On the first sync every record is new, and a burst of "you beat your
    5 km!" for times set in 2019 would be nonsense.

    The IntegrityError branch is the same situation _upsert_daily_metric
    handles: linking an account fires a 2-day sync and a 30-day backfill on
    purpose, so two runs can insert the same first-sight record at once.
    Whoever commits second re-reads and finds the row already there - which
    on the retry means "seen before", not "improved", so the loser of the
    race cannot turn a first sight into a congratulation.
    """
    improved = _apply_records(session, user_id, raw_records)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        improved = _apply_records(session, user_id, raw_records)
        session.commit()
    return improved


def format_records(session: Session, user_id: int) -> str:
    """Every stored record, grouped by sport."""
    rows = session.exec(
        select(PersonalRecord).where(PersonalRecord.user_id == user_id)
    ).all()
    if not rows:
        return (
            "🏆 *שיאים אישיים*\n\n"
            "עוד לא נמשכו שיאים. לחץ 🔄 סנכרון ונביא אותם מגרמין."
        )

    by_sport: dict[str, list] = {}
    for row in rows:
        meta = record_meta(row.record_type)
        if meta is None:
            continue
        by_sport.setdefault(meta[1], []).append((meta, row))

    lines = [_rtl("🏆 *השיאים האישיים שלך*"), ""]
    for sport, items in by_sport.items():
        lines.append(_rtl(f"*{SPORT_LABELS.get(sport, sport)}*"))
        # Ordered by the Garmin id so distances read short-to-long rather
        # than in whatever order the database returned them.
        for meta, row in sorted(items, key=lambda p: [k for k, v in RECORD_TYPES.items() if v[0] == p[0][0]][0]):
            when = f"  _({row.achieved_at.strftime('%d/%m/%y')})_" if row.achieved_at else ""
            lines.append(_rtl(f"   {meta[2]} — *{format_value(row.record_type, row.value)}*{when}"))
        lines.append("")

    return "\n".join(lines).rstrip()


def format_broken_records(improved: list[dict]) -> str:
    """The congratulation message. Only ever called with something to say."""
    lines = ["🏆 *שיא אישי חדש!*", ""]

    for item in improved:
        new = format_value(item["record_type"], item["value"])
        old = format_value(item["record_type"], item["previous"])
        lines.append(_rtl(f"{SPORT_LABELS.get(item['sport'], '')} *{item['label']}*"))
        lines.append(_rtl(f"   {new}  —  קודם {old}"))
        lines.append("")

    lines.append(_rtl("כל הכבוד! 💪 העבודה שעשית מתורגמת למספרים."))
    return "\n".join(lines)
