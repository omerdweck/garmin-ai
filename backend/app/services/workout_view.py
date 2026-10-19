"""How a workout reads in Telegram: one line per step, numbers the way an athlete says them."""

from app.services.workout_schema import ExerciseBlock, Repeat, Step, Workout, estimated_seconds

SPORT_LABELS = {
    "running": ("🏃", "ריצה"),
    "walking": ("🚶", "הליכה"),
    "hiking": ("🥾", "טיול"),
    "cycling": ("🚴", "רכיבה"),
    "swimming": ("🏊", "שחייה"),
    "strength": ("🏋️", "כוח"),
    "hiit": ("🔥", "HIIT"),
    "yoga": ("🧘", "יוגה"),
    "mobility": ("🤸", "מוביליטי"),
    "cardio": ("❤️", "קרדיו"),
}
KIND_LABELS = {
    "warmup": "חימום",
    "interval": "",
    "recovery": "התאוששות",
    "rest": "מנוחה",
    "cooldown": "שחרור",
    "other": "",
}
STROKE_LABELS = {"free": "חופשי", "back": "גב", "breast": "חזה", "fly": "פרפר", "im": "מעורב", "drill": "תרגיל", "any": ""}


def clock(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _distance(meters: int, sport: str) -> str:
    if sport == "swimming" or meters < 1000:
        return f"{meters} מ'"
    km = meters / 1000
    return f"{km:g} ק\"מ"


def _target(step: Step) -> str:
    t = step.target
    if t.type == "pace":
        return f"קצב {clock(t.low)}-{clock(t.high)}"
    if t.type == "heart_rate_zone":
        return f"אזור דופק {t.zone}"
    if t.type == "heart_rate":
        return f"דופק {t.low:.0f}-{t.high:.0f}"
    if t.type == "power":
        return f"{t.low:.0f}-{t.high:.0f}W"
    if t.type == "power_ftp_pct":
        return f"{t.low:.0f}-{t.high:.0f}% FTP"
    if t.type == "cadence":
        return f"קדנס {t.low:.0f}-{t.high:.0f}"
    return ""


def _step_line(step: Step, sport: str) -> str:
    if step.end == "distance":
        amount = _distance(step.meters, sport)
    elif step.end == "lap_button":
        amount = "עד לחיצה על Lap"
    else:
        amount = clock(step.seconds)
    parts = [p for p in (KIND_LABELS[step.kind], amount, STROKE_LABELS.get(step.stroke or "any", ""), _target(step)) if p]
    line = " · ".join(parts)
    return f"{line} _({step.note})_" if step.note else line


def _exercise_line(block: ExerciseBlock) -> str:
    work = f"{block.reps} חזרות" if block.reps is not None else clock(block.seconds)
    weight = f" · {block.weight_kg:g} ק\"ג" if block.weight_kg else ""
    rest = f" · מנוחה {clock(block.rest_seconds)}" if block.rest_seconds else ""
    return f"*{block.exercise}*: {block.sets}×{work}{weight}{rest}"


def describe(workout: Workout) -> str:
    emoji, label = SPORT_LABELS[workout.sport]
    minutes = round(estimated_seconds(workout) / 60)
    lines = [f"{emoji} *{workout.name}*", f"{label} · כ-{minutes} דק'"]
    if workout.date:
        lines[-1] += f" · {workout.date.strftime('%d.%m')}"
    if workout.sport == "swimming":
        lines[-1] += f" · בריכה {workout.pool_length_m} מ'"
    if workout.description:
        lines.append(f"_{workout.description}_")
    lines.append("")
    for item in workout.steps:
        if isinstance(item, ExerciseBlock):
            lines.append(f"• {_exercise_line(item)}")
        elif isinstance(item, Repeat):
            lines.append(f"• *{item.times} פעמים:*")
            lines += [f"    ◦ {_step_line(s, workout.sport)}" for s in item.steps]
        else:
            lines.append(f"• {_step_line(item, workout.sport)}")
    return "\n".join(lines)
