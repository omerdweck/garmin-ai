"""
Our schema -> Garmin's workout JSON, per sport. And, when a real Garmin
fixture exists for a sport (tools/capture_workout_fixtures.py), a check that
what we write has the same shape as what Garmin itself stores.
"""

import json
from pathlib import Path

import pytest

from app.services.workout_garmin import ConversionError, to_garmin
from app.services.workout_schema import SPORTS, parse_workout
from tests.workout_examples import EXAMPLES

FIXTURES = Path(__file__).parent / "fixtures" / "garmin_workouts"
# Until the owner captures them, there are none - the checks then skip, loudly.
FIXTURE_FILES = sorted(FIXTURES.glob("*.json")) or [None]


def convert(sport, ftp=250):
    workout, errors = parse_workout(EXAMPLES[sport])
    assert not errors
    return to_garmin(workout, ftp)


def walk(steps):
    for step in steps:
        yield step
        yield from walk(step.get("workoutSteps", []))


@pytest.mark.parametrize("sport", SPORTS)
def test_every_sport_converts(sport):
    payload = convert(sport)
    assert payload["workoutSegments"][0]["sportType"] == payload["sportType"]
    assert payload["estimatedDurationInSecs"] > 0
    json.dumps(payload)  # nothing that cannot be sent


@pytest.mark.parametrize("sport", SPORTS)
def test_step_orders_are_unique_and_sequential(sport):
    orders = [s["stepOrder"] for s in walk(convert(sport)["workoutSegments"][0]["workoutSteps"])]
    assert orders == list(range(1, len(orders) + 1))


def test_pace_becomes_speed_slow_end_first():
    steps = list(walk(convert("running")["workoutSegments"][0]["workoutSteps"]))
    interval = next(s for s in steps if s.get("targetType", {}).get("workoutTargetTypeKey") == "pace.zone")
    assert interval["targetValueOne"] == round(1000 / 285, 4)  # 4:45/km, the slower end
    assert interval["targetValueTwo"] == round(1000 / 270, 4)  # 4:30/km
    assert interval["endCondition"]["conditionTypeKey"] == "distance" and interval["endConditionValue"] == 1000


def test_repeat_group_carries_its_children():
    repeat = convert("running")["workoutSegments"][0]["workoutSteps"][1]
    assert repeat["type"] == "RepeatGroupDTO" and repeat["numberOfIterations"] == 5
    assert {s["childStepId"] for s in repeat["workoutSteps"]} == {repeat["childStepId"]}


def test_ftp_percent_becomes_watts():
    steps = list(walk(convert("cycling", ftp=200)["workoutSegments"][0]["workoutSteps"]))
    work = next(s for s in steps if s["stepType"]["stepTypeKey"] == "interval")
    assert (work["targetValueOne"], work["targetValueTwo"]) == (176, 188)


def test_ftp_percent_without_ftp_cannot_go_to_garmin():
    workout, _ = parse_workout(EXAMPLES["cycling"])
    with pytest.raises(ConversionError):
        to_garmin(workout, ftp=None)


def test_strength_uses_the_catalog_keys_and_grams():
    steps = list(walk(convert("strength")["workoutSegments"][0]["workoutSteps"]))
    squat = next(s for s in steps if s.get("exerciseName") == "BARBELL_BACK_SQUAT")
    assert squat["category"] == "SQUAT"
    assert squat["endCondition"]["conditionTypeKey"] == "reps" and squat["endConditionValue"] == 6
    assert squat["weightValue"] == 80_000
    plank = next(s for s in steps if s.get("category") == "PLANK")
    assert plank["endCondition"]["conditionTypeKey"] == "time" and plank["endConditionValue"] == 45


def test_swimming_has_pool_and_strokes():
    payload = convert("swimming")
    assert payload["poolLength"] == 25.0 and payload["poolLengthUnit"]["unitKey"] == "meter"
    steps = list(walk(payload["workoutSegments"][0]["workoutSteps"]))
    strokes = {s["strokeType"]["strokeTypeKey"] for s in steps if "strokeType" in s}
    assert {"free", "backstroke", "any_stroke"} <= strokes
    rest = next(s for s in steps if s["stepType"]["stepTypeKey"] == "rest")
    assert "strokeType" not in rest


def test_lap_button_step_has_no_value():
    cooldown = convert("running")["workoutSegments"][0]["workoutSteps"][-1]
    assert cooldown["endCondition"]["conditionTypeKey"] == "lap.button"
    assert "endConditionValue" not in cooldown


# --- against what Garmin itself stores -----------------------------------------------

def _shape(steps):
    """For each step type in a workout: the set of keys its steps carry."""
    shapes: dict = {}
    for step in walk(steps):
        shapes.setdefault(step["type"], set()).update(step)
    return shapes


@pytest.mark.parametrize("fixture", FIXTURE_FILES, ids=lambda p: p.stem if p else "none-captured")
def test_we_write_only_fields_garmin_uses(fixture):
    if fixture is None:
        pytest.skip("no Garmin workout fixtures yet - see tools/capture_workout_fixtures.py")
    real = json.loads(fixture.read_text())
    ours_sport = next(
        (s for s in SPORTS if convert(s)["sportType"]["sportTypeKey"] == real["sportType"]["sportTypeKey"]), None
    )
    if ours_sport is None:
        pytest.skip(f"no example for {real['sportType']['sportTypeKey']}")
    real_shape = _shape(real["workoutSegments"][0]["workoutSteps"])
    ours = _shape(convert(ours_sport)["workoutSegments"][0]["workoutSteps"])
    for step_type, keys in ours.items():
        if step_type in real_shape:
            assert keys <= real_shape[step_type] | {"description"}, f"{step_type}: {keys - real_shape[step_type]}"


@pytest.mark.parametrize("fixture", FIXTURE_FILES, ids=lambda p: p.stem if p else "none-captured")
def test_pace_order_matches_garmin(fixture):
    if fixture is None:
        pytest.skip("no Garmin workout fixtures yet - see tools/capture_workout_fixtures.py")
    real = json.loads(fixture.read_text())
    paces = [
        s for s in walk(real["workoutSegments"][0]["workoutSteps"])
        if s.get("targetType", {}).get("workoutTargetTypeKey") == "pace.zone"
    ]
    if not paces:
        pytest.skip("no pace target in this fixture")
    assert all(s["targetValueOne"] <= s["targetValueTwo"] for s in paces)
