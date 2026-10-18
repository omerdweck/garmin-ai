"""
The fixture capture script writes real Garmin data into git, so its scrub
is a privacy control and gets tested like one.
"""

import pytest

from tools.capture_garmin_fixtures import (
    FAKE_ACTIVITY_ID,
    _assert_clean,
    _scrub,
    _scrub_detail_stream,
)

REAL_ID = 24109347140

RAW = {
    "activityId": REAL_ID,
    "activityName": "Tel Aviv Running",
    "startLatitude": 32.08,
    "endLongitude": 34.78,
    "locationName": "Tel Aviv",
    "userProfileId": 987,
    "ownerDisplayName": "omer",
    "summaryDTO": {"averageHR": 150, "elevationGain": 42.0, "calculatedTotalTime": 1800},
    "lapDTOs": [{"lapIndex": 1, "startLatitude": 32.1, "averageSpeed": 3.2}],
    "metadataDTO": {"associatedActivityId": REAL_ID},
}


def test_location_and_identity_are_removed():
    clean = _scrub(RAW, REAL_ID)

    assert "startLatitude" not in clean
    assert "endLongitude" not in clean
    assert "locationName" not in clean
    assert "userProfileId" not in clean
    assert "ownerDisplayName" not in clean
    assert "Tel Aviv" not in repr(clean)  # Garmin's auto-name includes the city
    assert "startLatitude" not in clean["lapDTOs"][0]


def test_real_activity_id_is_replaced_everywhere():
    clean = _scrub(RAW, REAL_ID)

    assert clean["activityId"] == FAKE_ACTIVITY_ID
    assert clean["metadataDTO"]["associatedActivityId"] == FAKE_ACTIVITY_ID
    assert str(REAL_ID) not in repr(clean)


def test_training_data_is_kept():
    # Over-scrubbing is its own failure: "calculated" contains "lat", and a
    # careless pattern would strip the very fields the analysis needs.
    clean = _scrub(RAW, REAL_ID)

    assert clean["summaryDTO"] == {"averageHR": 150, "elevationGain": 42.0, "calculatedTotalTime": 1800}
    assert clean["lapDTOs"][0]["averageSpeed"] == 3.2


def test_positional_gps_columns_in_the_detail_stream_are_blanked():
    details = {
        "metricDescriptors": [
            {"key": "directHeartRate", "metricsIndex": 0},
            {"key": "directLatitude", "metricsIndex": 1},
            {"key": "directLongitude", "metricsIndex": 2},
        ],
        "activityDetailMetrics": [{"metrics": [140, 32.08, 34.78]}],
    }

    _scrub_detail_stream(details)

    assert details["activityDetailMetrics"][0]["metrics"] == [140, None, None]


def test_assert_clean_refuses_leftover_location():
    with pytest.raises(RuntimeError):
        _assert_clean({"laps": [{"endLatitude": 1.0}]})
