"""
The schema the coach writes workouts in: every sport accepted, nonsense
rejected with an error the coach can act on.
"""

import copy
import json

import pytest

from app.services.workout_schema import (
    SPORTS,
    estimated_seconds,
    parse_workout,
    resolve_exercise,
    search_exercises,
    tool_input_schema,
)
from tests.workout_examples import EXAMPLES


def errors_for(data):
    workout, errors = parse_workout(data)
    assert workout is None, "expected this to be rejected"
    return " | ".join(errors)


def test_there_is_an_example_for_every_sport():
    assert set(EXAMPLES) == set(SPORTS)


@pytest.mark.parametrize("sport", SPORTS)
def test_every_sport_is_accepted(sport):
    workout, errors = parse_workout(EXAMPLES[sport])
    assert errors == [] and workout.sport == sport


def _with(sport, path, value):
    data = copy.deepcopy(EXAMPLES[sport])
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return data


@pytest.mark.parametrize("pace", [(100, 120), (300, 2000)])
def test_impossible_running_pace_is_rejected(pace):
    data = _with("running", ["steps", 1, "steps", 0, "target"], {"type": "pace", "low": pace[0], "high": pace[1]})
    assert "sensible range" in errors_for(data)


def test_pace_bounds_the_wrong_way_round_are_rejected():
    data = _with("running", ["steps", 1, "steps", 0, "target"], {"type": "pace", "low": 300, "high": 270})
    assert "above high" in errors_for(data)


def test_heart_rate_zone_must_exist():
    data = _with("running", ["steps", 0, "target"], {"type": "heart_rate_zone", "zone": 7})
    assert "zone 1-5" in errors_for(data)


def test_a_target_from_another_sport_is_rejected():
    data = _with("running", ["steps", 0, "target"], {"type": "power", "low": 200, "high": 250})
    assert "not available for running" in errors_for(data)


def test_unknown_exercise_gets_suggestions():
    data = _with("strength", ["steps", 1, "exercise"], "back squat")
    message = errors_for(data)
    assert "not in Garmin's exercise catalog" in message and "Barbell Back Squat" in message


def test_exercise_needs_reps_or_seconds_not_both():
    data = _with("strength", ["steps", 1, "seconds"], 30)
    assert "either reps or seconds" in errors_for(data)


def test_exercise_blocks_only_in_strength():
    data = copy.deepcopy(EXAMPLES["running"])
    data["steps"].append({"kind": "exercise", "exercise": "Plank", "sets": 1, "seconds": 30})
    assert "strength workouts only" in errors_for(data)


def test_swimming_needs_a_pool_and_whole_lengths():
    data = copy.deepcopy(EXAMPLES["swimming"])
    del data["pool_length_m"]
    assert "pool_length_m" in errors_for(data)
    odd = _with("swimming", ["steps", 0, "meters"], 310)
    assert "whole number of 25 m lengths" in errors_for(odd)


def test_timed_sports_have_no_distance_steps():
    data = _with("yoga", ["steps", 0], {"kind": "other", "end": "distance", "meters": 1000})
    assert "cannot end by distance" in errors_for(data)


def test_a_six_hour_cap_on_the_whole_workout():
    data = _with("cycling", ["steps", 1, "times"], 50)
    data["steps"][1]["steps"][0]["seconds"] = 600
    assert "longer than 6 hours" in errors_for(data)


def test_unknown_fields_are_rejected():
    data = copy.deepcopy(EXAMPLES["yoga"])
    data["calories"] = 300
    assert "calories" in errors_for(data)


def test_estimated_duration_counts_repeats_and_paces():
    workout, _ = parse_workout(EXAMPLES["running"])
    # 600 warm-up + 5 x (1 km at the middle of 4:30-4:45, + 120 s) + a minute for the lap-button cool-down
    assert estimated_seconds(workout) == int(600 + 5 * (277.5 + 120) + 60)


def test_catalog_lookup():
    assert resolve_exercise("barbell back squat")["category"] == "SQUAT"
    assert resolve_exercise("SQUAT/BARBELL_BACK_SQUAT") is not None
    assert resolve_exercise("flying carpet") is None
    assert "Bench Press" in search_exercises("bench press")


def test_tool_schema_is_self_contained():
    schema = json.dumps(tool_input_schema())
    assert "$ref" not in schema and "$defs" not in schema
    assert '"sport"' in schema and '"steps"' in schema
