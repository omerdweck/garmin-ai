"""
Formats already-synced data into ready-to-send Telegram messages.

Deliberately does NOT involve Claude: these are the "just show me my
numbers" buttons, and running them through an LLM would cost tokens per
tap to restate values we already have exactly. Claude is only for the
conversational path, where interpretation is the actual product.

All text here is Hebrew, since it's user-facing bot output (unlike code
comments, which stay English).
"""

from datetime import date as date_type
from datetime import timedelta
from typing import Optional

from sqlmodel import Session, col, select

from app.models.activity import Activity
from app.models.daily_metric import DailyMetric
from app.models.user import User

# How far back to look for a metric that isn't recorded every day - a watch
# left on the charger leaves gaps in everything.
LOOKBACK_DAYS = 14

# VO2 max and fitness age get a longer window: Garmin only recalculates them
# after a qualifying activity (often weeks apart), and unlike sleep or
# stress they move slowly enough that a month-old reading is still true.
SLOW_METRIC_LOOKBACK_DAYS = 90

# Garmin's activityType.typeKey values are English snake_case; translate the
# common ones and fall back to the raw key for anything unmapped.
ACTIVITY_TYPE_LABELS = {
    "running": "🏃 ריצה",
    "treadmill_running": "🏃 ריצה (הליכון)",
    "trail_running": "⛰️ ריצת שטח",
    "cycling": "🚴 אופניים",
    "indoor_cycling": "🚴 אופניים (בית)",
    "lap_swimming": "🏊 שחייה",
    "open_water_swimming": "🏊 שחייה במים פתוחים",
    "strength_training": "🏋️ אימון כוח",
    "walking": "🚶 הליכה",
    "hiking": "🥾 טיול",
    "yoga": "🧘 יוגה",
    "cardio": "💪 קרדיו",
}

HRV_STATUS_LABELS = {
    "BALANCED": "מאוזן",
    "UNBALANCED": "לא מאוזן",
    "LOW": "נמוך",
    "POOR": "ירוד",
}


# Right-to-Left Mark. Prefixing a line with this pins its base direction to
# RTL, which is what stops Hebrew/English/number mixes from being visually
# reordered by the bidirectional algorithm - the original layout rendered
# "Body Battery: שיא 51" with the value jumping ahead of its own label.
RLM = "‏"


def _rtl(text: str) -> str:
    return f"{RLM}{text}"


def _format_minutes(minutes: Optional[int]) -> Optional[str]:
    if minutes is None:
        return None
    hours, mins = minutes // 60, minutes % 60
    return f"{hours} שעות ו-{mins} דקות" if mins else f"{hours} שעות"


def _date_note(value_date: Optional[date_type], reference: Optional[date_type]) -> str:
    """
    Marks a value that isn't from the most recent day. Rather than stamping
    every single line with a date (which was most of the visual clutter),
    only the stale ones say so - VO2 max is routinely weeks old, sleep
    shouldn't be.
    """
    if value_date is None or reference is None or value_date == reference:
        return ""
    return f" _({value_date.strftime('%d/%m')})_"


def _recent_metrics(session: Session, user_id: int, days: int = LOOKBACK_DAYS) -> list[DailyMetric]:
    """Most recent first, so callers can take [0] for latest or scan for the first non-null field."""
    cutoff = date_type.today() - timedelta(days=days)
    return list(
        session.exec(
            select(DailyMetric)
            .where(DailyMetric.user_id == user_id, DailyMetric.date >= cutoff)
            .order_by(col(DailyMetric.date).desc())
        ).all()
    )


def _latest_value(metrics: list[DailyMetric], field: str) -> tuple[object, Optional[date_type]]:
    """
    Finds the most recent non-null value of `field` and the date it came
    from. Returning the date matters: showing a VO2 max from 3 weeks ago as
    if it were today's would be misleading.
    """
    for metric in metrics:
        value = getattr(metric, field)
        if value is not None:
            return value, metric.date
    return None, None


def format_metrics_snapshot(session: Session, user_id: int) -> str:
    metrics = _recent_metrics(session, user_id)
    if not metrics:
        return "אין עדיין נתונים מסונכרנים 🤔\nנסה ללחוץ על 🔄 סנכרון עכשיו."

    today = metrics[0]
    slow_metrics = _recent_metrics(session, user_id, days=SLOW_METRIC_LOOKBACK_DAYS)

    lines: list[str] = [
        _rtl(f"📊 *המדדים שלך* · {today.date.strftime('%d/%m')}"),
    ]

    # --- Sleep ---------------------------------------------------------
    sleep_minutes, sleep_date = _latest_value(metrics, "sleep_duration_minutes")
    if sleep_minutes is not None:
        latest_sleep = next(m for m in metrics if m.date == sleep_date)
        lines.append("")
        lines.append(_rtl("😴 *שינה*"))
        lines.append(_rtl(f"{_format_minutes(sleep_minutes)}{_date_note(sleep_date, today.date)}"))
        stages = []
        if latest_sleep.deep_sleep_minutes is not None:
            stages.append(f"עמוקה {latest_sleep.deep_sleep_minutes}")
        if latest_sleep.rem_sleep_minutes is not None:
            stages.append(f"REM {latest_sleep.rem_sleep_minutes}")
        if latest_sleep.light_sleep_minutes is not None:
            stages.append(f"קלה {latest_sleep.light_sleep_minutes}")
        if stages:
            lines.append(_rtl(f"_{' · '.join(stages)} דקות_"))

    # --- Heart & fitness -----------------------------------------------
    heart_lines: list[str] = []

    resting_hr, hr_date = _latest_value(metrics, "resting_heart_rate")
    if resting_hr is not None:
        heart_lines.append(_rtl(f"❤️ דופק מנוחה — *{resting_hr}*{_date_note(hr_date, today.date)}"))

    hrv, hrv_date = _latest_value(metrics, "avg_hrv")
    if hrv is not None:
        status, _ = _latest_value(metrics, "hrv_status")
        status_text = f" · {HRV_STATUS_LABELS.get(status, status)}" if status else ""
        heart_lines.append(_rtl(f"💓 HRV — *{hrv}*{status_text}{_date_note(hrv_date, today.date)}"))

    vo2, vo2_date = _latest_value(slow_metrics, "vo2_max")
    if vo2 is not None:
        heart_lines.append(_rtl(f"🫁 VO₂ max — *{vo2:.1f}*{_date_note(vo2_date, today.date)}"))

    fitness_age, fitness_age_date = _latest_value(slow_metrics, "fitness_age")
    if fitness_age is not None:
        heart_lines.append(_rtl(f"🎂 גיל כושר — *{fitness_age}*{_date_note(fitness_age_date, today.date)}"))

    if heart_lines:
        lines.append("")
        lines.append(_rtl("*לב וכושר*"))
        lines.extend(heart_lines)

    # --- Recovery -------------------------------------------------------
    recovery_lines: list[str] = []

    body_battery, bb_date = _latest_value(metrics, "body_battery_high")
    if body_battery is not None:
        low, _ = _latest_value(metrics, "body_battery_low")
        # Range written low-to-high so the numbers read naturally inside an
        # RTL line, instead of "peak X (low Y)" which the bidi algorithm
        # was visually reversing.
        value = f"{low}–{body_battery}" if low is not None else f"{body_battery}"
        recovery_lines.append(_rtl(f"🔋 Body Battery — *{value}*{_date_note(bb_date, today.date)}"))

    stress, stress_date = _latest_value(metrics, "avg_stress_level")
    if stress is not None:
        recovery_lines.append(_rtl(f"😰 סטרס ממוצע — *{stress}*{_date_note(stress_date, today.date)}"))

    if recovery_lines:
        lines.append("")
        lines.append(_rtl("*התאוששות*"))
        lines.extend(recovery_lines)

    # --- Today's activity ----------------------------------------------
    activity_lines: list[str] = []
    if today.steps is not None:
        activity_lines.append(_rtl(f"👟 צעדים — *{today.steps:,}*"))
    if today.active_calories is not None:
        activity_lines.append(_rtl(f"🔥 קלוריות פעילות — *{today.active_calories:,}*"))
    if today.distance_meters:
        activity_lines.append(_rtl(f'📏 מרחק — *{today.distance_meters / 1000:.2f}* ק"מ'))
    intensity = (today.moderate_intensity_minutes or 0) + (today.vigorous_intensity_minutes or 0)
    if intensity:
        activity_lines.append(_rtl(f"⚡ דקות עצימות — *{intensity}*"))

    if activity_lines:
        lines.append("")
        lines.append(_rtl("*הפעילות היום*"))
        lines.extend(activity_lines)

    return "\n".join(lines)


def format_recent_activities(session: Session, user_id: int, limit: int = 5) -> str:
    activities = list(
        session.exec(
            select(Activity)
            .where(Activity.user_id == user_id)
            .order_by(col(Activity.start_time).desc())
            .limit(limit)
        ).all()
    )

    if not activities:
        return "עדיין לא נמצאו אימונים מסונכרנים 🤔\nנסה ללחוץ על 🔄 סנכרון עכשיו."

    lines = [_rtl("🏃 *האימונים האחרונים שלך*")]
    for activity in activities:
        label = ACTIVITY_TYPE_LABELS.get(activity.activity_type or "", activity.activity_type or "אימון")
        lines.append("")
        lines.append(_rtl(f"*{label}* · {activity.start_time.strftime('%d/%m %H:%M')}"))

        details = []
        if activity.distance_meters:
            details.append(f'{activity.distance_meters / 1000:.2f} ק"מ')
        if activity.duration_seconds:
            details.append(f"{int(activity.duration_seconds // 60)} דקות")
        if activity.distance_meters and activity.duration_seconds and activity.distance_meters > 100:
            pace_sec_per_km = activity.duration_seconds / (activity.distance_meters / 1000)
            details.append(f'{int(pace_sec_per_km // 60)}:{int(pace_sec_per_km % 60):02d} לק"מ')
        if activity.avg_heart_rate:
            details.append(f"דופק {int(activity.avg_heart_rate)}")
        if activity.calories:
            details.append(f"{int(activity.calories)} קל'")

        if details:
            lines.append(_rtl(f"_{' · '.join(details)}_"))

    return "\n".join(lines)


def format_status(session: Session, user: User) -> str:
    """Account/settings overview - what's linked, when it last synced, summary time."""
    from app.models.garmin_account import GarminAccount

    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()

    lines = [_rtl("⚙️ *ההגדרות שלך*"), ""]
    if account is None:
        lines.append(_rtl("🔗 גרמין — *לא מחובר*"))
    else:
        lines.append(_rtl("🔗 גרמין — *מחובר* ✅"))
        if account.last_sync_at:
            lines.append(_rtl(f"🔄 סנכרון אחרון — {account.last_sync_at.strftime('%d/%m %H:%M')}"))
        if account.last_sync_error:
            lines.append(_rtl("⚠️ הסנכרון האחרון נכשל - נסה שוב מאוחר יותר"))

    if user.daily_summary_hour is None:
        lines.append(_rtl("🔔 סיכום יומי — *כבוי*"))
    else:
        lines.append(_rtl(f"🔔 סיכום יומי — כל יום ב-*{user.daily_summary_hour:02d}:00*"))

    if user.training_goal:
        lines.append(_rtl(f"🎯 מטרה — {user.training_goal}"))

    lines.append("")
    lines.append(_rtl("_סנכרון אוטומטי רץ פעמיים ביום, ב-08:00 וב-20:00_"))
    return "\n".join(lines)
