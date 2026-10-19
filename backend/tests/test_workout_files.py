"""Trainer files (.zwo / .erg / .mrc): valid, with the right power numbers."""

import copy
import xml.etree.ElementTree as ET

import pytest

from app.services.workout_files import FileError, available_formats, to_erg, to_mrc, to_zwo
from app.services.workout_schema import parse_workout
from tests.workout_examples import EXAMPLES


def cycling(**changes):
    data = copy.deepcopy(EXAMPLES["cycling"])
    data.update(changes)
    workout, errors = parse_workout(data)
    assert not errors
    return workout


def test_zwo_is_valid_xml_with_intervals():
    root = ET.fromstring(to_zwo(cycling()))
    workout = root.find("workout")
    tags = [child.tag for child in workout]
    assert tags == ["Warmup", "IntervalsT", "Cooldown"]
    intervals = workout.find("IntervalsT")
    assert intervals.get("Repeat") == "3" and intervals.get("OnPower") == "0.910" and intervals.get("OffPower") == "0.550"
    warmup = workout.find("Warmup")
    assert (warmup.get("PowerLow"), warmup.get("PowerHigh")) == ("0.500", "0.750")
    cooldown = workout.find("Cooldown")
    assert (cooldown.get("PowerLow"), cooldown.get("PowerHigh")) == ("0.600", "0.400")  # ramps down


def test_zwo_escapes_names():
    root = ET.fromstring(to_zwo(cycling(name="Hills & <fun>")))
    assert root.find("name").text == "Hills & <fun>"


def test_steps_without_power_are_free_ride_in_zwo_but_refused_in_erg():
    data = copy.deepcopy(EXAMPLES["cycling"])
    data["steps"][0]["target"] = {"type": "none"}
    workout, _ = parse_workout(data)
    assert ET.fromstring(to_zwo(workout)).find("workout")[0].tag == "FreeRide"
    with pytest.raises(FileError):
        to_mrc(workout)


def test_mrc_has_percent_points_and_unrolls_repeats():
    text = to_mrc(cycling())
    assert "MINUTES PERCENT" in text
    data_rows = text.split("[COURSE DATA]\n")[1].split("[END COURSE DATA]")[0].strip().splitlines()
    assert len(data_rows) == 2 * (1 + 3 * 2 + 1)
    assert data_rows[0] == "0.00\t50" and data_rows[1] == "10.00\t75"
    # 10 warm-up + 3 x (10 + 5) + 10 cool-down = 65 minutes, ending at 40%
    assert data_rows[-1] == "65.00\t40"


def test_erg_needs_ftp_and_writes_watts():
    with pytest.raises(FileError):
        to_erg(cycling(), ftp=None)
    text = to_erg(cycling(), ftp=200)
    assert "FTP = 200" in text and "MINUTES WATTS" in text
    assert "10.00\t182" in text  # interval at 91% of 200


def test_distance_steps_cannot_go_to_a_trainer():
    data = copy.deepcopy(EXAMPLES["cycling"])
    data["steps"][0] = {"kind": "warmup", "end": "distance", "meters": 5000}
    workout, _ = parse_workout(data)
    with pytest.raises(FileError):
        to_zwo(workout)


def test_only_cycling_gets_trainer_files():
    workout, _ = parse_workout(EXAMPLES["running"])
    assert available_formats(workout, ftp=250) == []
    assert available_formats(cycling(), ftp=None) == ["zwo", "mrc"]
    assert available_formats(cycling(), ftp=250) == ["zwo", "erg", "mrc"]
