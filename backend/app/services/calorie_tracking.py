"""
Calorie intake tracking, and the balance against what Garmin measured.

Deliberately no Claude, like metrics_view: intake is a number the user typed
and burn is a number Garmin measured, so restating them through a model would
spend tokens to say what we already know exactly.

The whole feature is off unless User.daily_calorie_target is set. That single
field is the switch - there is no separate boolean to drift out of sync with
it, and every entry point here checks it.

The one judgement call worth stating: burn means Garmin's *total* daily
expenditure, BMR included, not the calories attributed to workouts. Comparing
a 2,000 kcal intake against a 400 kcal workout would report an enormous
surplus every single day, which is worse than reporting nothing.
"""

import logging
import re
from datetime import date as date_type
from datetime import timedelta
from typing import Optional

from sqlmodel import Session, col, func, select

from app.models.calorie_entry import CalorieBurnOverride, CalorieEntry
from app.models.daily_metric import DailyMetric
from app.models.user import User

logger = logging.getLogger(__name__)

RLM = "‏"

# Generous bounds. The point is to reject a typo or a stray word, not to
# argue with someone eating unusually little or unusually much.
MIN_CALORIES = 1
MAX_ENTRY_CALORIES = 10_000
MIN_TARGET = 800
MAX_TARGET = 8_000

# Days without a single entry before the bot offers to stop tracking. Long
# enough to survive a weekend away, short enough that a daily reminder is not
# still arriving weeks after someone quietly gave up.
ABANDON_AFTER_DAYS = 5

# How far back weekly_average_burn looks for a stand-in figure.
AVERAGE_WINDOW_DAYS = 7

# Where a burn figure came from. The caller labels the number accordingly -
# presenting an estimate as a measurement is the one thing this must not do.
SOURCE_GARMIN = "garmin"
SOURCE_MANUAL = "manual"
SOURCE_ESTIMATED = "estimated"
SOURCE_NONE = "none"


def _rtl(text: str) -> str:
    return f"{RLM}{text}"


def is_tracking(user: User) -> bool:
    return user.daily_calorie_target is not None


def parse_calories(text: str) -> Optional[int]:
    """
    A calorie count from whatever the user typed, or None.

    Accepts "1800", "1800 קלוריות", "1,800" - people write a number the way
    they say it, and rejecting the word would make the form feel broken.
    """
    text = (text or "").strip().replace(",", "")
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers:
        return None
    value = int(round(float(numbers[0])))
    return value if MIN_CALORIES <= value <= MAX_ENTRY_CALORIES else None


def parse_target(text: str) -> Optional[int]:
    """Same, with the tighter bounds that make sense for a daily target."""
    value = parse_calories(text)
    if value is None:
        return None
    return value if MIN_TARGET <= value <= MAX_TARGET else None


def set_target(session: Session, user_id: int, calories: Optional[int]) -> None:
    """
    Turns tracking on, or off when passed None.

    Switching off deliberately leaves the entries in place, the same way
    disconnecting Garmin keeps its data: someone who turns tracking back on
    next month should find their history, not a blank slate.
    """
    user = session.get(User, user_id)
    if user is None:
        return
    user.daily_calorie_target = calories
    if calories is None:
        user.calorie_reminder_hour_1 = None
        user.calorie_reminder_hour_2 = None
        user.calorie_reminder_hour_3 = None
        user.calorie_abandon_prompted_at = None
    session.add(user)
    session.commit()


def reminder_hours(user: User) -> list[int]:
    hours = [
        user.calorie_reminder_hour_1,
        user.calorie_reminder_hour_2,
        user.calorie_reminder_hour_3,
    ]
    return sorted(h for h in hours if h is not None)


def set_reminder_hours(session: Session, user_id: int, hours: list[int]) -> None:
    """Three slots, filled in order; anything beyond the third is dropped."""
    user = session.get(User, user_id)
    if user is None:
        return
    ordered = sorted(set(hours))[:3]
    padded = ordered + [None] * (3 - len(ordered))
    user.calorie_reminder_hour_1, user.calorie_reminder_hour_2, user.calorie_reminder_hour_3 = padded
    session.add(user)
    session.commit()


def add_entry(
    session: Session,
    user_id: int,
    calories: int,
    on_date: Optional[date_type] = None,
) -> CalorieEntry:
    entry = CalorieEntry(
        user_id=user_id,
        entry_date=on_date or date_type.today(),
        calories=calories,
    )
    session.add(entry)

    # Logging something ends any abandonment episode, so the next silent
    # stretch gets its own single prompt rather than being suppressed by an
    # answer given weeks ago.
    user = session.get(User, user_id)
    if user is not None and user.calorie_abandon_prompted_at is not None:
        user.calorie_abandon_prompted_at = None
        session.add(user)

    session.commit()
    session.refresh(entry)
    return entry


def consumed_on(session: Session, user_id: int, day: Optional[date_type] = None) -> int:
    """Every entry for that day, added up. Zero when nothing was logged."""
    day = day or date_type.today()
    total = session.exec(
        select(func.coalesce(func.sum(CalorieEntry.calories), 0)).where(
            CalorieEntry.user_id == user_id,
            CalorieEntry.entry_date == day,
        )
    ).one()
    return int(total or 0)


def entries_on(session: Session, user_id: int, day: Optional[date_type] = None) -> list[CalorieEntry]:
    day = day or date_type.today()
    return list(
        session.exec(
            select(CalorieEntry)
            .where(CalorieEntry.user_id == user_id, CalorieEntry.entry_date == day)
            .order_by(col(CalorieEntry.created_at))
        ).all()
    )


def delete_entry(session: Session, user_id: int, entry_id: int) -> bool:
    """Scoped by user_id: the id arrives from callback data the client sends."""
    entry = session.exec(
        select(CalorieEntry).where(
            CalorieEntry.id == entry_id,
            CalorieEntry.user_id == user_id,
        )
    ).first()
    if entry is None:
        return False
    session.delete(entry)
    session.commit()
    return True


def set_burn_override(session: Session, user_id: int, calories: int, day: Optional[date_type] = None) -> None:
    """One figure per day, replacing rather than adding."""
    day = day or date_type.today()
    existing = session.exec(
        select(CalorieBurnOverride).where(
            CalorieBurnOverride.user_id == user_id,
            CalorieBurnOverride.date == day,
        )
    ).first()
    if existing is None:
        existing = CalorieBurnOverride(user_id=user_id, date=day, calories=calories)
    else:
        existing.calories = calories
    session.add(existing)
    session.commit()


def weekly_average_burn(session: Session, user_id: int) -> Optional[int]:
    """
    The user's own average total expenditure over the last week, for days the
    watch actually recorded. Their own average rather than a formula: it is
    already calibrated to this person, and it needs no body measurements we
    might have wrong.
    """
    cutoff = date_type.today() - timedelta(days=AVERAGE_WINDOW_DAYS)
    average = session.exec(
        select(func.avg(DailyMetric.total_calories)).where(
            DailyMetric.user_id == user_id,
            DailyMetric.date >= cutoff,
            col(DailyMetric.total_calories).is_not(None),
        )
    ).one()
    return int(round(average)) if average else None


def burned_on(session: Session, user_id: int, day: Optional[date_type] = None) -> tuple[Optional[int], str]:
    """
    Total expenditure for a day, and where the figure came from.

    Order matters: a value the user entered themselves beats Garmin's, because
    they entered it precisely for a day Garmin got wrong or missed.

    Never falls through to an estimate on its own - SOURCE_NONE is returned
    and the caller offers the user the choice. Quietly substituting an average
    would put a number nobody measured into a balance the user acts on.
    """
    day = day or date_type.today()

    override = session.exec(
        select(CalorieBurnOverride).where(
            CalorieBurnOverride.user_id == user_id,
            CalorieBurnOverride.date == day,
        )
    ).first()
    if override is not None:
        return override.calories, SOURCE_MANUAL

    metric = session.exec(
        select(DailyMetric).where(DailyMetric.user_id == user_id, DailyMetric.date == day)
    ).first()
    if metric is not None and metric.total_calories is not None:
        return metric.total_calories, SOURCE_GARMIN

    return None, SOURCE_NONE


def days_since_last_entry(session: Session, user_id: int) -> Optional[int]:
    """None when the user has never logged anything."""
    latest = session.exec(
        select(func.max(CalorieEntry.entry_date)).where(CalorieEntry.user_id == user_id)
    ).one()
    if latest is None:
        return None
    return (date_type.today() - latest).days


SOURCE_LABELS = {
    SOURCE_GARMIN: "",
    SOURCE_MANUAL: " _(הזנת ידנית)_",
    SOURCE_ESTIMATED: " _(הערכה לפי ממוצע השבוע)_",
}


def format_balance(session: Session, user_id: int, day: Optional[date_type] = None) -> str:
    """
    The 🍽 screen: what went in, what went out, and the gap between them.

    When burn is unknown the gap is simply not shown. A balance is a
    subtraction, and half a subtraction presented as a balance is a number the
    user would act on that means nothing.
    """
    day = day or date_type.today()
    user = session.get(User, user_id)
    if user is None or not is_tracking(user):
        return ""

    target = user.daily_calorie_target
    consumed = consumed_on(session, user_id, day)
    burned, source = burned_on(session, user_id, day)

    lines = [_rtl("🍽 *מאזן קלוריות - היום*"), ""]
    lines.append(_rtl(f"נצרך: *{consumed:,}* מתוך יעד של *{target:,}*"))

    remaining = target - consumed
    if remaining > 0:
        lines.append(_rtl(f"נותרו לך *{remaining:,}* קלוריות ליעד"))
    elif remaining < 0:
        lines.append(_rtl(f"עברת את היעד ב-*{abs(remaining):,}*"))
    else:
        lines.append(_rtl("בדיוק ביעד 🎯"))

    lines.append("")

    if burned is None:
        lines.append(_rtl("🔥 שריפה: *אין נתון* - השעון לא נענד היום"))
        lines.append(_rtl("_בלי נתון שריפה אי אפשר לחשב מאזן._"))
    else:
        lines.append(_rtl(f"🔥 נשרף: *{burned:,}*{SOURCE_LABELS.get(source, '')}"))
        balance = consumed - burned
        if balance < 0:
            lines.append(_rtl(f"📉 גירעון של *{abs(balance):,}* קלוריות"))
        elif balance > 0:
            lines.append(_rtl(f"📈 עודף של *{balance:,}* קלוריות"))
        else:
            lines.append(_rtl("⚖️ מאוזן בדיוק"))

    entries = entries_on(session, user_id, day)
    if entries:
        lines += ["", _rtl(f"_{len(entries)} רישומים היום: " + " + ".join(f"{e.calories:,}" for e in entries) + "_")]

    return "\n".join(lines)
