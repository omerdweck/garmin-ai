"""
One workout schema for every sport - what the coach writes, what we store,
and what every converter (Garmin, .zwo, .erg) reads.

The coach never writes Garmin's JSON. It writes this, which is small enough
to describe in a tool definition and strict enough to check: a pace of
1:30/km, a heart-rate zone 7 or an exercise Garmin does not know is
rejected here with a message the coach can act on, so nothing broken is
ever stored - let alone sent to someone's watch.

Units, everywhere:
    pace    seconds per km ("low" is the faster end)
    power   watts, or percent of FTP for power_ftp_pct
    time    seconds;  distance  metres;  weight  kilograms
"""

import difflib
from datetime import date as date_type
from functools import lru_cache
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.garmin_client import exercise_catalog

SPORTS = ["running", "walking", "hiking", "cycling", "swimming", "strength", "hiit", "yoga", "mobility", "cardio"]
Sport = Literal["running", "walking", "hiking", "cycling", "swimming", "strength", "hiit", "yoga", "mobility", "cardio"]

ON_FOOT = {"running", "walking", "hiking"}
TIMED_ONLY = {"hiit", "yoga", "mobility", "cardio"}

ALLOWED_TARGETS = {
    "running": {"none", "pace", "heart_rate_zone", "heart_rate", "cadence"},
    "walking": {"none", "pace", "heart_rate_zone", "heart_rate", "cadence"},
    "hiking": {"none", "pace", "heart_rate_zone", "heart_rate"},
    "cycling": {"none", "power", "power_ftp_pct", "heart_rate_zone", "heart_rate", "cadence"},
    "swimming": {"none"},
    "strength": {"none"},
    "hiit": {"none", "heart_rate_zone"},
    "yoga": {"none"},
    "mobility": {"none"},
    "cardio": {"none", "heart_rate_zone", "heart_rate"},
}
ALLOWED_ENDS = {
    **{s: {"time", "distance", "lap_button"} for s in ("running", "walking", "hiking", "cycling", "swimming")},
    **{s: {"time", "lap_button"} for s in ("strength", "hiit", "yoga", "mobility", "cardio")},
}
PACE_RANGE = {"running": (150, 900), "walking": (300, 1500), "hiking": (300, 1800)}
CADENCE_RANGE = {"running": (120, 230), "walking": (60, 160), "cycling": (40, 140)}

MAX_STEP_SECONDS = 4 * 3600
MAX_WORKOUT_SECONDS = 6 * 3600
MAX_TOP_LEVEL_STEPS = 40

# Used only to estimate how long a distance step takes when it has no pace.
DEFAULT_SECONDS_PER_KM = {"running": 360, "walking": 600, "hiking": 720, "cycling": 120, "swimming": 1500}


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["none", "pace", "heart_rate_zone", "heart_rate", "power", "power_ftp_pct", "cadence"] = "none"
    low: Optional[float] = Field(default=None, description="Lower bound (for pace: the FASTER end, s/km)")
    high: Optional[float] = Field(default=None, description="Upper bound (for pace: the SLOWER end, s/km)")
    zone: Optional[int] = Field(default=None, description="Heart-rate zone 1-5, for heart_rate_zone only")


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["warmup", "interval", "recovery", "rest", "cooldown", "other"]
    end: Literal["time", "distance", "lap_button"] = "time"
    seconds: Optional[int] = None
    meters: Optional[int] = None
    target: Target = Field(default_factory=Target)
    stroke: Optional[Literal["any", "free", "back", "breast", "fly", "im", "drill"]] = None
    note: Optional[str] = Field(default=None, max_length=120)


class Repeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["repeat"]
    times: int = Field(ge=2, le=50)
    steps: list[Step] = Field(min_length=1, max_length=10)


class ExerciseBlock(BaseModel):
    """N sets of one strength exercise, with rest between sets."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["exercise"]
    exercise: str = Field(description="Exercise name exactly as in Garmin's catalog, e.g. 'Barbell Back Squat'")
    sets: int = Field(ge=1, le=10)
    reps: Optional[int] = Field(default=None, ge=1, le=100)
    seconds: Optional[int] = Field(default=None, ge=5, le=600, description="For timed exercises (plank) instead of reps")
    weight_kg: Optional[float] = Field(default=None, ge=0, le=400)
    rest_seconds: int = Field(default=60, ge=0, le=600)


Item = Annotated[Union[Step, Repeat, ExerciseBlock], Field(discriminator="kind")]


class Workout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sport: Sport
    name: str = Field(min_length=1, max_length=60)
    description: Optional[str] = Field(default=None, max_length=300)
    date: Optional[date_type] = Field(default=None, description="The day it is meant for, if any")
    pool_length_m: Optional[int] = Field(default=None, description="Swimming only: 25 or 50 usually")
    steps: list[Item] = Field(min_length=1, max_length=MAX_TOP_LEVEL_STEPS)

    @model_validator(mode="after")
    def _check(self) -> "Workout":
        errors = sport_errors(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


# --- the strength catalog ---------------------------------------------------

@lru_cache(maxsize=1)
def _catalog_by_name() -> dict[str, dict]:
    return {entry["name"].lower(): entry for entry in exercise_catalog()}


def resolve_exercise(name: str) -> Optional[dict]:
    """An exact catalog name (any case), or 'CATEGORY/EXERCISE' as Garmin spells it."""
    entry = _catalog_by_name().get(name.strip().lower())
    if entry is not None:
        return entry
    if "/" in name:
        category, _, exercise = name.strip().upper().partition("/")
        for candidate in _catalog_by_name().values():
            if candidate["category"] == category and candidate["exercise"] == exercise:
                return candidate
    return None


def similar_exercises(name: str, limit: int = 5) -> list[str]:
    names = [e["name"] for e in _catalog_by_name().values()]
    close = difflib.get_close_matches(name, names, n=limit, cutoff=0.5)
    if len(close) < limit:
        words = [w for w in name.lower().split() if len(w) > 2]
        close += [n for n in names if n not in close and words and all(w in n.lower() for w in words)][: limit - len(close)]
    return close


def search_exercises(query: str, limit: int = 15) -> list[str]:
    words = [w for w in query.lower().split() if w]
    names = [e["name"] for e in _catalog_by_name().values()]
    # Shortest first: "barbell squat" should find "Barbell Back Squat"
    # before "Elevated Front-foot Barbell Split Squat".
    hits = sorted((n for n in names if all(w in n.lower() for w in words)), key=len)
    return (hits or similar_exercises(query, limit))[:limit]


# --- per-sport rules ----------------------------------------------------------

def _target_errors(sport: str, where: str, t: Target) -> list[str]:
    if t.type not in ALLOWED_TARGETS[sport]:
        return [f"{where}: target '{t.type}' is not available for {sport} (allowed: {sorted(ALLOWED_TARGETS[sport])})"]
    if t.type == "none":
        return []
    if t.type == "heart_rate_zone":
        return [] if t.zone in (1, 2, 3, 4, 5) else [f"{where}: heart_rate_zone needs zone 1-5"]
    if t.low is None or t.high is None:
        return [f"{where}: target '{t.type}' needs both low and high"]
    if t.low > t.high:
        return [f"{where}: low ({t.low}) is above high ({t.high})"]
    bounds = {
        "pace": PACE_RANGE.get(sport),
        "heart_rate": (60, 220),
        "power": (30, 1500),
        "power_ftp_pct": (30, 200),
        "cadence": CADENCE_RANGE.get(sport),
    }[t.type]
    if not (bounds[0] <= t.low and t.high <= bounds[1]):
        unit = "s/km" if t.type == "pace" else ""
        return [f"{where}: {t.type} {t.low}-{t.high}{unit} is outside the sensible range {bounds[0]}-{bounds[1]}"]
    return []


def _step_errors(w: Workout, where: str, s: Step) -> list[str]:
    errors = []
    if s.end not in ALLOWED_ENDS[w.sport]:
        errors.append(f"{where}: a {w.sport} step cannot end by {s.end}")
    if s.end == "time" and not (s.seconds and 5 <= s.seconds <= MAX_STEP_SECONDS):
        errors.append(f"{where}: a time step needs seconds between 5 and {MAX_STEP_SECONDS}")
    if s.end == "distance":
        if not (s.meters and 25 <= s.meters <= 200_000):
            errors.append(f"{where}: a distance step needs meters between 25 and 200000")
        elif w.sport == "swimming" and w.pool_length_m and s.meters % w.pool_length_m:
            errors.append(f"{where}: {s.meters} m is not a whole number of {w.pool_length_m} m lengths")
    if s.end != "time" and s.seconds is not None and s.kind != "rest":
        errors.append(f"{where}: seconds is only for time steps")
    if s.stroke is not None and w.sport != "swimming":
        errors.append(f"{where}: stroke is for swimming only")
    errors += _target_errors(w.sport, where, s.target)
    return errors


def sport_errors(w: Workout) -> list[str]:
    errors = []
    if w.sport == "swimming":
        if w.pool_length_m not in range(10, 101):
            errors.append("swimming needs pool_length_m (e.g. 25 or 50)")
    elif w.pool_length_m is not None:
        errors.append("pool_length_m is for swimming only")

    for index, item in enumerate(w.steps, start=1):
        where = f"step {index}"
        if isinstance(item, ExerciseBlock):
            if w.sport != "strength":
                errors.append(f"{where}: exercise blocks are for strength workouts only")
                continue
            if (item.reps is None) == (item.seconds is None):
                errors.append(f"{where}: give either reps or seconds")
            if resolve_exercise(item.exercise) is None:
                suggestions = similar_exercises(item.exercise)
                hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else " Use search_exercises."
                errors.append(f"{where}: '{item.exercise}' is not in Garmin's exercise catalog.{hint}")
        elif isinstance(item, Repeat):
            for inner_index, inner in enumerate(item.steps, start=1):
                errors += _step_errors(w, f"{where}.{inner_index}", inner)
        else:
            errors += _step_errors(w, where, item)

    if not errors and estimated_seconds(w) > MAX_WORKOUT_SECONDS:
        errors.append(f"the whole workout is longer than {MAX_WORKOUT_SECONDS // 3600} hours")
    return errors


# --- reading a workout ----------------------------------------------------------

def _step_seconds(sport: str, s: Step) -> float:
    if s.end == "time" or (s.kind == "rest" and s.seconds):
        return float(s.seconds or 0)
    if s.end == "distance" and s.meters:
        if s.target.type == "pace":
            per_km = (s.target.low + s.target.high) / 2
        else:
            per_km = DEFAULT_SECONDS_PER_KM.get(sport, 360)
        return s.meters / 1000 * per_km
    return 60.0  # lap button: unknown, count a minute


def estimated_seconds(w: Workout) -> int:
    total = 0.0
    for item in w.steps:
        if isinstance(item, ExerciseBlock):
            work = item.seconds if item.seconds is not None else (item.reps or 0) * 4
            total += item.sets * (work + item.rest_seconds)
        elif isinstance(item, Repeat):
            total += item.times * sum(_step_seconds(w.sport, s) for s in item.steps)
        else:
            total += _step_seconds(w.sport, item)
    return int(total)


def parse_workout(data: dict) -> tuple[Optional[Workout], list[str]]:
    """(workout, []) or (None, errors) - errors written for the coach to fix."""
    try:
        return Workout.model_validate(data), []
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            message = error["msg"].removeprefix("Value error, ")
            messages.append(f"{location}: {message}" if location else message)
        return None, messages


def _inline_refs(schema: dict) -> dict:
    """The JSON schema with every $ref expanded - a tool definition stays self-contained."""
    defs = schema.pop("$defs", {})

    def expand(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return expand(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: expand(v) for k, v in node.items() if k not in ("title", "discriminator")}
        if isinstance(node, list):
            return [expand(v) for v in node]
        return node

    return expand(schema)


def tool_input_schema() -> dict:
    return _inline_refs(Workout.model_json_schema())
