"""
Per-activity-type formatting.

A run and a gym session are not the same shape of thing: pace, cadence
and elevation say something about a run and nothing about a bench press,
while sets, reps and which muscles were worked are meaningless for a
swim. So rather than one formatter showing the lowest common denominator
(duration / distance / heart rate) for everything, each type gets its own
field list, drawn from what Garmin actually returns for it.

Everything here reads Activity.raw_json, which is the complete original
API response - the structured columns only hold the handful of fields
common to all types.
"""

from typing import Callable, Optional

from sqlmodel import Session, col, select

from app.models.activity import Activity

RLM = "‏"


def _rtl(text: str) -> str:
    return f"{RLM}{text}"


ACTIVITY_TYPE_LABELS = {
    "running": "🏃 ריצה",
    "treadmill_running": "🏃 ריצה (הליכון)",
    "trail_running": "⛰️ ריצת שטח",
    "indoor_running": "🏃 ריצה מקורה",
    "cycling": "🚴 אופניים",
    "indoor_cycling": "🚴 אופניים מקורה",
    "mountain_biking": "🚵 אופני הרים",
    "lap_swimming": "🏊 שחייה בבריכה",
    "open_water_swimming": "🌊 שחייה במים פתוחים",
    "strength_training": "🏋️ אימון כוח",
    "indoor_cardio": "💪 קרדיו מקורה",
    "cardio": "💪 קרדיו",
    "walking": "🚶 הליכה",
    "hiking": "🥾 טיול",
    "yoga": "🧘 יוגה",
    "pilates": "🤸 פילאטיס",
    "elliptical": "🏃 אליפטיקל",
    "rowing": "🚣 חתירה",
    "stair_climbing": "🪜 מדרגות",
    "hiit": "🔥 HIIT",
}


def type_label(activity_type: Optional[str]) -> str:
    return ACTIVITY_TYPE_LABELS.get(activity_type or "", f"🏅 {activity_type or 'אימון'}")


# Garmin classifies each strength set into an exercise category. Mapping
# those to muscle groups is what turns "BENCH_PRESS x8" into the thing a
# user actually wants to know - which muscles the session hit.
EXERCISE_MUSCLE_GROUPS = {
    "BENCH_PRESS": "חזה",
    "PUSH_UP": "חזה",
    "FLYE": "חזה",
    "CHEST_PRESS": "חזה",
    "SHOULDER_PRESS": "כתפיים",
    "LATERAL_RAISE": "כתפיים",
    "FRONT_RAISE": "כתפיים",
    "SHRUG": "כתפיים",
    "REVERSE_FLYE": "כתפיים",
    "CURL": "יד קדמית",
    "BICEPS_CURL": "יד קדמית",
    "HAMMER_CURL": "יד קדמית",
    "TRICEPS_EXTENSION": "יד אחורית",
    "DIP": "יד אחורית",
    "SKULL_CRUSHER": "יד אחורית",
    "ROW": "גב",
    "PULL_UP": "גב",
    "PULLDOWN": "גב",
    "LAT_PULLDOWN": "גב",
    "DEADLIFT": "גב ורגליים",
    "SQUAT": "רגליים",
    "LUNGE": "רגליים",
    "LEG_PRESS": "רגליים",
    "LEG_CURL": "רגליים",
    "LEG_EXTENSION": "רגליים",
    "CALF_RAISE": "שוקיים",
    "HIP_RAISE": "ישבן",
    "HIP_THRUST": "ישבן",
    "GLUTE_BRIDGE": "ישבן",
    "CRUNCH": "בטן",
    "SIT_UP": "בטן",
    "PLANK": "core",
    "CORE": "core",
    "CARDIO": "קרדיו",
    "TOTAL_BODY": "כל הגוף",
}

TRAINING_EFFECT_LABELS = {
    "VO2MAX": "שיפור VO₂ max",
    "AEROBIC_BASE": "בסיס אירובי",
    "TEMPO": "טמפו",
    "LACTATE_THRESHOLD": "סף לקטט",
    "ANAEROBIC_CAPACITY": "יכולת אנאירובית",
    "SPEED": "מהירות",
    "RECOVERY": "התאוששות",
    "UNKNOWN": None,
}


def _get(activity: Activity, key: str):
    return (activity.raw_json or {}).get(key)


def _fmt_duration(seconds: Optional[float]) -> Optional[str]:
    if not seconds:
        return None
    total = int(seconds)
    hours, minutes, secs = total // 3600, (total % 3600) // 60, total % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d} שעות"
    return f"{minutes}:{secs:02d} דקות"


def _fmt_pace(seconds: Optional[float], meters: Optional[float]) -> Optional[str]:
    if not seconds or not meters or meters < 100:
        return None
    pace = seconds / (meters / 1000)
    return f"{int(pace // 60)}:{int(pace % 60):02d}"


def _heart_lines(activity: Activity) -> list[str]:
    lines = []
    if activity.avg_heart_rate:
        max_hr = f" · שיא {int(activity.max_heart_rate)}" if activity.max_heart_rate else ""
        lines.append(_rtl(f"❤️ דופק ממוצע — *{int(activity.avg_heart_rate)}*{max_hr}"))
    return lines


def _effort_lines(activity: Activity) -> list[str]:
    """Training-effect fields Garmin reports for every activity type."""
    lines = []
    aerobic = _get(activity, "aerobicTrainingEffect")
    anaerobic = _get(activity, "anaerobicTrainingEffect")
    if aerobic:
        lines.append(_rtl(f"📈 אפקט אירובי — *{aerobic:.1f}* מתוך 5"))
    if anaerobic:
        lines.append(_rtl(f"⚡ אפקט אנאירובי — *{anaerobic:.1f}* מתוך 5"))

    label = TRAINING_EFFECT_LABELS.get(_get(activity, "trainingEffectLabel") or "")
    if label:
        lines.append(_rtl(f"🎯 סוג העומס — *{label}*"))

    load = _get(activity, "activityTrainingLoad")
    if load and load > 1:
        lines.append(_rtl(f"🏋️ עומס אימון — *{load:.0f}*"))

    battery = _get(activity, "differenceBodyBattery")
    if battery:
        lines.append(_rtl(f"🔋 שינוי ב-Body Battery — *{battery}*"))
    return lines


def _hr_zone_lines(activity: Activity) -> list[str]:
    """
    Time per heart-rate zone. Shown as minutes rather than raw seconds, and
    only for zones actually used, so a workout that stayed in zone 2 doesn't
    print five lines of zeros.
    """
    zones = [(i, _get(activity, f"hrTimeInZone_{i}") or 0) for i in range(1, 6)]
    # At least a full minute: a lower threshold prints "zone 1 - 0 minutes",
    # which is noise rather than information.
    used = [(i, secs) for i, secs in zones if secs >= 60]
    if not used:
        return []

    # "אזור" and not the transliterated "זון/זונה": that transliteration
    # collides with a Hebrew slur, which is not something to put in front
    # of users. "אזורי דופק" is also the standard Hebrew fitness term.
    lines = [_rtl("*זמן באזורי דופק*")]
    for zone, seconds in used:
        lines.append(_rtl(f"   אזור {zone} — {int(seconds // 60)} דקות"))
    return lines


# --------------------------------------------------------------------------
# Per-type formatters
# --------------------------------------------------------------------------


def _format_running(activity: Activity) -> list[str]:
    lines = []
    if activity.distance_meters:
        lines.append(_rtl(f'📏 מרחק — *{activity.distance_meters / 1000:.2f}* ק"מ'))
    duration = _fmt_duration(activity.duration_seconds)
    if duration:
        lines.append(_rtl(f"⏱ משך — *{duration}*"))
    pace = _fmt_pace(activity.duration_seconds, activity.distance_meters)
    if pace:
        lines.append(_rtl(f'⚡ קצב ממוצע — *{pace}* לק"מ'))

    lines.extend(_heart_lines(activity))

    cadence = _get(activity, "averageRunningCadenceInStepsPerMinute")
    if cadence:
        lines.append(_rtl(f"👟 קצב צעדים — *{cadence:.0f}* לדקה"))
    stride = _get(activity, "avgStrideLength")
    if stride:
        lines.append(_rtl(f"📐 אורך צעד — *{stride / 100:.2f}* מטר"))
    gain = _get(activity, "elevationGain")
    if gain:
        lines.append(_rtl(f"⛰ עלייה מצטברת — *{gain:.0f}* מטר"))
    power = _get(activity, "avgPower")
    if power:
        lines.append(_rtl(f"💪 הספק ממוצע — *{power:.0f}* וואט"))
    if activity.calories:
        lines.append(_rtl(f"🔥 קלוריות — *{int(activity.calories)}*"))
    if activity.steps:
        lines.append(_rtl(f"👣 צעדים — *{activity.steps:,}*"))

    vo2 = _get(activity, "vO2MaxValue")
    if vo2:
        lines.append(_rtl(f"🫁 VO₂ max שנמדד — *{vo2:.0f}*"))

    best_km = _get(activity, "fastestSplit_1000")
    if best_km:
        lines.append(_rtl(f'🏅 הק"מ המהיר ביותר — *{int(best_km // 60)}:{int(best_km % 60):02d}*'))
    return lines


def _format_swimming(activity: Activity) -> list[str]:
    lines = []
    if activity.distance_meters:
        lines.append(_rtl(f"📏 מרחק — *{activity.distance_meters:.0f}* מטר"))
    duration = _fmt_duration(activity.duration_seconds)
    if duration:
        lines.append(_rtl(f"⏱ משך — *{duration}*"))

    if activity.duration_seconds and activity.distance_meters:
        per_100 = activity.duration_seconds / (activity.distance_meters / 100)
        lines.append(_rtl(f"⚡ קצב — *{int(per_100 // 60)}:{int(per_100 % 60):02d}* ל-100 מטר"))

    lines.extend(_heart_lines(activity))

    lengths = _get(activity, "activeLengths")
    if lengths:
        lines.append(_rtl(f"🔁 מספר בריכות — *{int(lengths)}*"))
    strokes = _get(activity, "strokes")
    if strokes:
        lines.append(_rtl(f"🤽 סה\"כ חתירות — *{int(strokes)}*"))
    avg_strokes = _get(activity, "avgStrokes")
    if avg_strokes:
        lines.append(_rtl(f"   _ממוצע {avg_strokes:.1f} לבריכה_"))
    cadence = _get(activity, "averageSwimCadenceInStrokesPerMinute")
    if cadence:
        lines.append(_rtl(f"🎵 קצב חתירה — *{cadence:.0f}* לדקה"))
    swolf = _get(activity, "averageSwolf")
    if swolf:
        # SWOLF is niche enough that the number alone means nothing to most
        # people - the one-line explanation is what makes it useful.
        lines.append(_rtl(f"🎯 SWOLF — *{swolf:.0f}*"))
        lines.append(_rtl("   _מדד יעילות שחייה: זמן + חתירות. נמוך יותר = טוב יותר_"))
    if activity.calories:
        lines.append(_rtl(f"🔥 קלוריות — *{int(activity.calories)}*"))
    return lines


def _format_strength(activity: Activity) -> list[str]:
    lines = []
    duration = _fmt_duration(activity.duration_seconds)
    if duration:
        lines.append(_rtl(f"⏱ משך — *{duration}*"))

    active_sets = _get(activity, "activeSets")
    total_sets = _get(activity, "totalSets")
    if active_sets or total_sets:
        lines.append(_rtl(f"🔢 סטים — *{int(active_sets or total_sets)}*"))
    reps = _get(activity, "totalReps")
    if reps:
        lines.append(_rtl(f"🔁 סה\"כ חזרות — *{int(reps)}*"))

    lines.extend(_heart_lines(activity))
    if activity.calories:
        lines.append(_rtl(f"🔥 קלוריות — *{int(activity.calories)}*"))

    lines.extend(_muscle_lines(activity))
    return lines


def _muscle_lines(activity: Activity) -> list[str]:
    """
    Turns the per-set exercise categories into muscle groups. Garmin gives
    several candidate exercises per set with a confidence score, so the
    highest-scoring known one wins - and sets it couldn't classify are
    simply left out rather than reported as "unknown".
    """
    sets = (activity.exercise_sets or {}).get("exerciseSets")
    if not sets:
        return []

    muscle_reps: dict[str, int] = {}
    exercise_reps: dict[str, int] = {}

    for entry in sets:
        if entry.get("setType") != "ACTIVE":
            continue
        candidates = [e for e in (entry.get("exercises") or []) if e.get("category") not in (None, "UNKNOWN")]
        if not candidates:
            continue
        best = max(candidates, key=lambda e: e.get("probability") or 0)
        category = best["category"]
        reps = entry.get("repetitionCount") or 0

        muscle = EXERCISE_MUSCLE_GROUPS.get(category)
        if muscle:
            muscle_reps[muscle] = muscle_reps.get(muscle, 0) + reps
        exercise_reps[category] = exercise_reps.get(category, 0) + reps

    if not muscle_reps and not exercise_reps:
        return []

    lines = ["", _rtl("*שרירים שעבדו*")]
    for muscle, reps in sorted(muscle_reps.items(), key=lambda kv: -kv[1]):
        lines.append(_rtl(f"   💪 {muscle} — {reps} חזרות"))

    if exercise_reps:
        lines.append("")
        lines.append(_rtl("*תרגילים שזוהו*"))
        for category, reps in sorted(exercise_reps.items(), key=lambda kv: -kv[1])[:6]:
            pretty = category.replace("_", " ").title()
            lines.append(_rtl(f"   • {pretty} — {reps} חזרות"))
        lines.append(_rtl("_זיהוי התרגילים אוטומטי ע\"י גרמין, ייתכנו אי-דיוקים_"))
    return lines


def _format_generic(activity: Activity) -> list[str]:
    """Fallback for types without a dedicated formatter - still better than nothing."""
    lines = []
    duration = _fmt_duration(activity.duration_seconds)
    if duration:
        lines.append(_rtl(f"⏱ משך — *{duration}*"))
    if activity.distance_meters:
        lines.append(_rtl(f'📏 מרחק — *{activity.distance_meters / 1000:.2f}* ק"מ'))
        pace = _fmt_pace(activity.duration_seconds, activity.distance_meters)
        if pace:
            lines.append(_rtl(f'⚡ קצב — *{pace}* לק"מ'))
    lines.extend(_heart_lines(activity))
    if activity.calories:
        lines.append(_rtl(f"🔥 קלוריות — *{int(activity.calories)}*"))
    if activity.steps:
        lines.append(_rtl(f"👣 צעדים — *{activity.steps:,}*"))
    return lines


FORMATTERS: dict[str, Callable[[Activity], list[str]]] = {
    "running": _format_running,
    "treadmill_running": _format_running,
    "trail_running": _format_running,
    "indoor_running": _format_running,
    "walking": _format_running,
    "hiking": _format_running,
    "lap_swimming": _format_swimming,
    "open_water_swimming": _format_swimming,
    "strength_training": _format_strength,
}


def format_activity_detail(activity: Activity) -> str:
    formatter = FORMATTERS.get(activity.activity_type or "", _format_generic)

    lines = [
        _rtl(f"*{type_label(activity.activity_type)}*"),
        _rtl(f"_{activity.start_time.strftime('%A, %d/%m/%Y · %H:%M')}_"),
        "",
    ]
    lines.extend(formatter(activity))

    effort = _effort_lines(activity)
    if effort:
        lines.append("")
        lines.extend(effort)

    zones = _hr_zone_lines(activity)
    if zones:
        lines.append("")
        lines.extend(zones)

    return "\n".join(lines)


def activity_type_counts(session: Session, user_id: int) -> list[tuple[str, int]]:
    """
    Types this user actually has, most frequent first. Deliberately not
    every type Garmin supports - a menu full of activities you've never
    done is noise, not choice.
    """
    activities = session.exec(select(Activity).where(Activity.user_id == user_id)).all()
    counts: dict[str, int] = {}
    for activity in activities:
        key = activity.activity_type or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def recent_of_type(session: Session, user_id: int, activity_type: str, limit: int = 8) -> list[Activity]:
    return list(
        session.exec(
            select(Activity)
            .where(Activity.user_id == user_id, Activity.activity_type == activity_type)
            .order_by(col(Activity.start_time).desc())
            .limit(limit)
        ).all()
    )


def summary_line(activity: Activity) -> str:
    """One-line label for the picker list - date plus the stat that matters for that type."""
    date = activity.start_time.strftime("%d/%m")
    if activity.activity_type == "strength_training":
        duration = int((activity.duration_seconds or 0) // 60)
        return f"{date} · {duration} דק'"
    if activity.distance_meters and activity.distance_meters > 100:
        unit = "מ'" if "swimming" in (activity.activity_type or "") else 'ק"מ'
        value = (
            f"{activity.distance_meters:.0f}"
            if unit == "מ'"
            else f"{activity.distance_meters / 1000:.1f}"
        )
        return f"{date} · {value} {unit}"
    duration = int((activity.duration_seconds or 0) // 60)
    return f"{date} · {duration} דק'"
