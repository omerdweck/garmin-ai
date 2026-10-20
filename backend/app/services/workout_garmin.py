"""
Our workout schema -> the JSON Garmin Connect's workout service accepts.
A pure function: no requests, so every sport is tested on its own.

The ids are Garmin's (from /workout-service/workout/types, as published in
garminconnect 0.3.11's workout module). Fields marked UNVERIFIED follow how
Garmin Connect's own exports look but have not been checked on a real
watch yet - which is why uploading is enabled per sport, only after a
manual check on the owner's watch (see WORKOUT_UPLOAD_SPORTS).
"""

from typing import Optional

from app.services.workout_schema import ExerciseBlock, Repeat, Step, Workout, estimated_seconds, resolve_exercise

SPORT_TYPES = {
    "running": (1, "running"),
    "cycling": (2, "cycling"),
    "swimming": (4, "swimming"),
    "strength": (5, "strength_training"),
    "cardio": (6, "cardio_training"),
    "yoga": (7, "yoga"),
    "hiit": (9, "hiit"),
    "mobility": (11, "mobility"),
    "walking": (17, "walking"),
    "hiking": (18, "hiking"),
}
STEP_TYPES = {"warmup": 1, "cooldown": 2, "interval": 3, "recovery": 4, "rest": 5, "repeat": 6, "other": 7}
END_CONDITIONS = {"lap.button": 1, "time": 2, "distance": 3, "iterations": 7, "fixed.rest": 8, "reps": 10}
TARGET_TYPES = {"no.target": 1, "power.zone": 2, "cadence": 3, "heart.rate.zone": 4, "pace.zone": 6}
# UNVERIFIED: stroke ids, in Garmin Connect's order.
STROKES = {
    "any": (1, "any_stroke"),
    "back": (2, "backstroke"),
    "breast": (3, "breaststroke"),
    "drill": (4, "drill"),
    "fly": (5, "fly"),
    "free": (6, "free"),
    "im": (7, "individual_medley"),
}
KILOGRAM = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
METRE = {"unitId": 1, "unitKey": "meter", "factor": 100.0}


class ConversionError(ValueError):
    """The workout is valid, but cannot be expressed for Garmin as it stands (e.g. %FTP without an FTP)."""


def _sport(workout: Workout) -> dict:
    type_id, key = SPORT_TYPES[workout.sport]
    return {"sportTypeId": type_id, "sportTypeKey": key, "displayOrder": type_id}


def _step_type(kind: str) -> dict:
    return {"stepTypeId": STEP_TYPES[kind], "stepTypeKey": kind, "displayOrder": STEP_TYPES[kind]}


def _end(key: str) -> dict:
    return {
        "conditionTypeId": END_CONDITIONS[key],
        "conditionTypeKey": key,
        "displayOrder": END_CONDITIONS[key],
        "displayable": key != "iterations",
    }


def _target(key: str) -> dict:
    return {"workoutTargetTypeId": TARGET_TYPES[key], "workoutTargetTypeKey": key, "displayOrder": TARGET_TYPES[key]}


def _target_fields(step: Step, ftp: Optional[int]) -> dict:
    t = step.target
    if t.type == "none":
        return {"targetType": _target("no.target")}
    if t.type == "heart_rate_zone":
        return {"targetType": _target("heart.rate.zone"), "zoneNumber": t.zone}
    if t.type == "pace":
        # Garmin stores pace as speed in m/s. UNVERIFIED: value one is the
        # slower end, value two the faster - as in Garmin Connect's exports.
        return {
            "targetType": _target("pace.zone"),
            "targetValueOne": round(1000 / t.high, 4),
            "targetValueTwo": round(1000 / t.low, 4),
        }
    if t.type == "power_ftp_pct":
        if not ftp:
            raise ConversionError("power targets in %FTP need the athlete's FTP in their profile")
        return {
            "targetType": _target("power.zone"),
            "targetValueOne": round(ftp * t.low / 100),
            "targetValueTwo": round(ftp * t.high / 100),
        }
    key = {"heart_rate": "heart.rate.zone", "power": "power.zone", "cadence": "cadence"}[t.type]
    return {"targetType": _target(key), "targetValueOne": t.low, "targetValueTwo": t.high}


class _Counter:
    def __init__(self):
        self.order = 0
        self.repeats = 0

    def next(self) -> int:
        self.order += 1
        return self.order


def _executable(workout: Workout, step: Step, counter: _Counter, ftp, child: Optional[int]) -> dict:
    swimming = workout.sport == "swimming"
    if step.kind == "rest" and swimming:
        end_key, value = "fixed.rest", float(step.seconds or 0)  # UNVERIFIED for swim rest
    elif step.end == "lap_button":
        end_key, value = "lap.button", None
    elif step.end == "distance":
        end_key, value = "distance", float(step.meters)
    else:
        end_key, value = "time", float(step.seconds)

    result = {
        "type": "ExecutableStepDTO",
        "stepOrder": counter.next(),
        "stepType": _step_type(step.kind),
        "endCondition": _end(end_key),
        "endConditionValue": value,
        "childStepId": child,
        **_target_fields(step, ftp),
    }
    if step.note:
        result["description"] = step.note
    if swimming and step.kind != "rest":
        stroke_id, stroke_key = STROKES[step.stroke or "any"]
        result["strokeType"] = {"strokeTypeId": stroke_id, "strokeTypeKey": stroke_key, "displayOrder": stroke_id}
    return result


def _repeat(times: int, children_builder, counter: _Counter) -> dict:
    counter.repeats += 1
    child = counter.repeats
    group = {
        "type": "RepeatGroupDTO",
        "stepOrder": counter.next(),
        "stepType": _step_type("repeat"),
        "numberOfIterations": times,
        "endCondition": _end("iterations"),
        "endConditionValue": float(times),
        "childStepId": child,
        "smartRepeat": False,
    }
    group["workoutSteps"] = children_builder(child)
    return group


def _exercise(block: ExerciseBlock, counter: _Counter) -> dict:
    entry = resolve_exercise(block.exercise)

    def children(child: int) -> list[dict]:
        work = {
            "type": "ExecutableStepDTO",
            "stepOrder": counter.next(),
            "stepType": _step_type("interval"),
            "endCondition": _end("reps" if block.reps is not None else "time"),
            "endConditionValue": float(block.reps if block.reps is not None else block.seconds),
            "targetType": _target("no.target"),
            "category": entry["category"],
            "exerciseName": entry["exercise"],
            "childStepId": child,
        }
        if block.weight_kg:
            work["weightValue"] = block.weight_kg * 1000.0  # Garmin stores grams
            work["weightUnit"] = dict(KILOGRAM)
        steps = [work]
        if block.rest_seconds:
            steps.append({
                "type": "ExecutableStepDTO",
                "stepOrder": counter.next(),
                "stepType": _step_type("rest"),
                "endCondition": _end("time"),
                "endConditionValue": float(block.rest_seconds),
                "targetType": _target("no.target"),
                "childStepId": child,
            })
        return steps

    if block.sets == 1:
        return children(None)
    return _repeat(block.sets, children, counter)


def to_garmin(workout: Workout, ftp: Optional[int] = None) -> dict:
    counter = _Counter()
    steps: list[dict] = []
    for item in workout.steps:
        if isinstance(item, ExerciseBlock):
            built = _exercise(item, counter)
            steps.extend(built if isinstance(built, list) else [built])
        elif isinstance(item, Repeat):
            steps.append(_repeat(
                item.times,
                lambda child, item=item: [_executable(workout, s, counter, ftp, child) for s in item.steps],
                counter,
            ))
        else:
            steps.append(_executable(workout, item, counter, ftp, None))

    payload = {
        "workoutName": workout.name,
        "description": workout.description,
        "sportType": _sport(workout),
        "estimatedDurationInSecs": estimated_seconds(workout),
        "workoutSegments": [{"segmentOrder": 1, "sportType": _sport(workout), "workoutSteps": steps}],
    }
    if workout.sport == "swimming":
        payload["poolLength"] = float(workout.pool_length_m)
        payload["poolLengthUnit"] = dict(METRE)
    return _drop_none(payload)


def _drop_none(node):
    if isinstance(node, dict):
        return {k: _drop_none(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [_drop_none(v) for v in node]
    return node
