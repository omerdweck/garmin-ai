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


def _format_minutes(minutes: Optional[int]) -> Optional[str]:
    if minutes is None:
        return None
    return f"{minutes // 60} שעות ו-{minutes % 60} דקות"


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

    lines: list[str] = ["📊 *המדדים האחרונים שלך*", ""]

    sleep_minutes, sleep_date = _latest_value(metrics, "sleep_duration_minutes")
    if sleep_minutes is not None:
        latest = next(m for m in metrics if m.date == sleep_date)
        lines.append(f"😴 *שינה* ({sleep_date.strftime('%d/%m')}): {_format_minutes(sleep_minutes)}")
        stages = []
        if latest.deep_sleep_minutes is not None:
            stages.append(f"עמוקה {latest.deep_sleep_minutes} ד'")
        if latest.rem_sleep_minutes is not None:
            stages.append(f"REM {latest.rem_sleep_minutes} ד'")
        if latest.light_sleep_minutes is not None:
            stages.append(f"קלה {latest.light_sleep_minutes} ד'")
        if stages:
            lines.append(f"   _{' · '.join(stages)}_")

    resting_hr, hr_date = _latest_value(metrics, "resting_heart_rate")
    if resting_hr is not None:
        lines.append(f"❤️ *דופק מנוחה* ({hr_date.strftime('%d/%m')}): {resting_hr} פעימות/דקה")

    hrv, hrv_date = _latest_value(metrics, "avg_hrv")
    if hrv is not None:
        status, _ = _latest_value(metrics, "hrv_status")
        status_text = f" ({HRV_STATUS_LABELS.get(status, status)})" if status else ""
        lines.append(f"💓 *HRV* ({hrv_date.strftime('%d/%m')}): {hrv} ms{status_text}")

    # Searched over a wider window than the rest - see SLOW_METRIC_LOOKBACK_DAYS.
    slow_metrics = _recent_metrics(session, user_id, days=SLOW_METRIC_LOOKBACK_DAYS)
    vo2, vo2_date = _latest_value(slow_metrics, "vo2_max")
    if vo2 is not None:
        lines.append(f"🫁 *VO₂ max* ({vo2_date.strftime('%d/%m')}): {vo2:.1f}")
    fitness_age, fitness_age_date = _latest_value(slow_metrics, "fitness_age")
    if fitness_age is not None:
        lines.append(f"🎂 *גיל כושר* ({fitness_age_date.strftime('%d/%m')}): {fitness_age}")

    body_battery, bb_date = _latest_value(metrics, "body_battery_high")
    if body_battery is not None:
        low, _ = _latest_value(metrics, "body_battery_low")
        low_text = f" (נמוך: {low})" if low is not None else ""
        lines.append(f"🔋 *Body Battery* ({bb_date.strftime('%d/%m')}): שיא {body_battery}{low_text}")

    stress, stress_date = _latest_value(metrics, "avg_stress_level")
    if stress is not None:
        lines.append(f"😰 *סטרס ממוצע* ({stress_date.strftime('%d/%m')}): {stress}")

    today = metrics[0]
    lines.append("")
    lines.append(f"*היום ({today.date.strftime('%d/%m')})*")
    lines.append(f"👟 צעדים: {today.steps:,}" if today.steps is not None else "👟 צעדים: אין נתון עדיין")
    if today.active_calories is not None:
        lines.append(f"🔥 קלוריות פעילות: {today.active_calories:,}")
    if today.distance_meters:
        lines.append(f"📏 מרחק: {today.distance_meters / 1000:.2f} ק\"מ")
    intensity = (today.moderate_intensity_minutes or 0) + (today.vigorous_intensity_minutes or 0)
    if intensity:
        lines.append(f"⚡ דקות עצימות: {intensity}")

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

    lines = [f"🏃 *{len(activities)} האימונים האחרונים שלך*", ""]
    for activity in activities:
        label = ACTIVITY_TYPE_LABELS.get(activity.activity_type or "", activity.activity_type or "אימון")
        lines.append(f"{label} — {activity.start_time.strftime('%d/%m %H:%M')}")

        details = []
        if activity.distance_meters:
            details.append(f'{activity.distance_meters / 1000:.2f} ק"מ')
        if activity.duration_seconds:
            minutes = int(activity.duration_seconds // 60)
            details.append(f"{minutes} דקות")
        if activity.distance_meters and activity.duration_seconds and activity.distance_meters > 100:
            pace_sec_per_km = activity.duration_seconds / (activity.distance_meters / 1000)
            details.append(f'קצב {int(pace_sec_per_km // 60)}:{int(pace_sec_per_km % 60):02d}/ק"מ')
        if activity.avg_heart_rate:
            details.append(f"דופק ממוצע {int(activity.avg_heart_rate)}")
        if activity.calories:
            details.append(f"{int(activity.calories)} קלוריות")

        if details:
            lines.append(f"   _{' · '.join(details)}_")
        lines.append("")

    return "\n".join(lines).strip()


def format_status(session: Session, user: User) -> str:
    """Account/settings overview - what's linked, when it last synced, summary time."""
    from app.models.garmin_account import GarminAccount

    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == user.id)).first()

    lines = ["⚙️ *ההגדרות שלך*", ""]
    if account is None:
        lines.append("🔗 גרמין: *לא מחובר*")
    else:
        lines.append("🔗 גרמין: *מחובר* ✅")
        if account.last_sync_at:
            lines.append(f"🔄 סנכרון אחרון: {account.last_sync_at.strftime('%d/%m %H:%M')}")
        if account.last_sync_error:
            lines.append("⚠️ הסנכרון האחרון נכשל - נסה שוב מאוחר יותר")

    if user.daily_summary_hour is None:
        lines.append("🔔 סיכום יומי: *כבוי*")
    else:
        lines.append(f"🔔 סיכום יומי: כל יום ב-{user.daily_summary_hour:02d}:00")

    if user.training_goal:
        lines.append(f"🎯 מטרה: {user.training_goal}")

    lines.append("")
    lines.append("_הסנכרון האוטומטי רץ פעמיים ביום: 08:00 ו-20:00_")
    return "\n".join(lines)
