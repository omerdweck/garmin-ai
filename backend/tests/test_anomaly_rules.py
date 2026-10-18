"""
The rules on literal numbers: each fires on the pattern it describes, stays
quiet on ordinary noise, and says "unknown" rather than "fine" when data is
missing.
"""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.services import anomaly_rules as r

END = date(2026, 10, 18)
T = r.Thresholds(
    min_baseline_days=14, hrv_std_factor=0.5, rhr_delta=5, sleep_min_minutes=360, sleep_min_score=60,
    body_battery_recovery=50, easy_run_drop_pct=4.0, acwr_max=1.5, volume_jump_pct=30.0,
    min_weekly_minutes=90, min_chronic_load=50,
)


def days(*values):
    """Values for consecutive days ending on END, oldest first."""
    return {END - timedelta(days=len(values) - 1 - i): v for i, v in enumerate(values) if v is not None}


def base(mean, std=3.0):
    return r.Baseline(mean=mean, std=std, samples=28)


def test_thresholds_come_from_settings():
    assert r.Thresholds.from_settings() == T


def test_baseline_needs_enough_days():
    assert r.make_baseline([50] * 13, 14) is None
    b = r.make_baseline([48, 52] * 7 + [None, None], 14)
    assert (b.mean, b.std, b.samples) == (50, 2, 14)


# --- HRV -----------------------------------------------------------------

def test_hrv_three_falls_ending_low_fires():
    found = r.hrv_drop(days(55, 52, 49, 44), END, base(54, 4), T)
    assert found.status == r.FIRING and found.details == {"values": [52, 49, 44], "baseline": 54}


def test_hrv_falling_but_still_normal_is_clear():
    # Three small falls inside the usual range are noise, not a trend.
    assert r.hrv_drop(days(60, 58, 57, 55), END, base(54, 4), T).status == r.CLEAR


def test_hrv_low_but_not_falling_is_clear():
    assert r.hrv_drop(days(44, 46, 43, 44), END, base(54, 4), T).status == r.CLEAR


def test_hrv_missing_night_is_unknown():
    assert r.hrv_drop(days(55, None, 49, 44), END, base(54, 4), T).status == r.UNKNOWN
    assert r.hrv_drop(days(55, 52, 49, 44), END, None, T).status == r.UNKNOWN


def test_hrv_is_judged_against_the_athletes_own_normal():
    # The same four nights: alarming for someone who usually sits at 70,
    # perfectly normal for someone who usually sits at 40.
    nights = days(55, 52, 49, 44)
    assert r.hrv_drop(nights, END, base(70, 5), T).status == r.FIRING
    assert r.hrv_drop(nights, END, base(40, 5), T).status == r.CLEAR


# --- resting HR ----------------------------------------------------------

def test_rhr_two_of_three_high_fires():
    assert r.rhr_high(days(57, 51, 58), END, base(51), T).status == r.FIRING


def test_rhr_one_high_day_is_clear():
    assert r.rhr_high(days(51, 52, 58), END, base(51), T).status == r.CLEAR


def test_rhr_just_under_the_delta_is_clear():
    assert r.rhr_high(days(55, 55, 55), END, base(51), T).status == r.CLEAR


def test_rhr_threshold_is_inclusive():
    assert r.rhr_high(days(56, 56, 50), END, base(51), T).status == r.FIRING


def test_rhr_same_numbers_different_athletes():
    week = days(58, 59, 57)
    assert r.rhr_high(week, END, base(51), T).status == r.FIRING
    assert r.rhr_high(week, END, base(57), T).status == r.CLEAR


# --- sleep ---------------------------------------------------------------

def test_two_short_nights_fire():
    found = r.sleep_poor(days(330, 420, 340), days(70, 80, 72), END, T)
    assert found.status == r.FIRING and found.details["poor"] == 2


def test_low_score_counts_as_poor_even_when_long():
    assert r.sleep_poor(days(450, 460, 470), days(55, 58, 80), END, T).status == r.FIRING


def test_one_bad_night_is_clear():
    assert r.sleep_poor(days(330, 420, 430), days(70, 80, 82), END, T).status == r.CLEAR


def test_sleep_needs_two_nights_of_data():
    assert r.sleep_poor(days(None, None, 300), days(None, None, 50), END, T).status == r.UNKNOWN


# --- Body Battery ----------------------------------------------------------

def test_three_days_without_recharge_fire():
    assert r.body_battery_low(days(45, 38, 49), END, T).status == r.FIRING


def test_one_good_recharge_clears():
    assert r.body_battery_low(days(45, 50, 40), END, T).status == r.CLEAR


def test_body_battery_gap_is_unknown():
    assert r.body_battery_low(days(45, None, 40), END, T).status == r.UNKNOWN


# --- easy runs -----------------------------------------------------------

def test_easy_runs_needing_more_beats_fire():
    found = r.easy_run_drift([1.12, 1.05, 1.06], base(1.15, 0.03), T)
    assert found.status == r.FIRING and found.details["drop_pct"] > 4


def test_one_slow_easy_run_is_clear():
    assert r.easy_run_drift([1.14, 1.05, 1.16], base(1.15, 0.03), T).status == r.CLEAR


def test_easy_runs_within_their_usual_spread_are_clear():
    # 5% below the mean, but this athlete's runs always vary that much.
    assert r.easy_run_drift([1.09, 1.09, 1.09], base(1.15, 0.08), T).status == r.CLEAR


def test_easy_runs_need_three_recent_and_a_baseline():
    assert r.easy_run_drift([1.0, 1.0], base(1.15), T).status == r.UNKNOWN
    assert r.easy_run_drift([1.0, 1.0, 1.0], None, T).status == r.UNKNOWN


# --- load ----------------------------------------------------------------

def test_high_acute_chronic_ratio_fires():
    found = r.load_spike(640, 400, None, None, T)
    assert found.status == r.FIRING and found.details == {"acwr": 1.6}


def test_volume_jump_fires():
    found = r.load_spike(None, None, 330, 220, T)
    assert found.status == r.FIRING and found.details["jump_pct"] == 50


def test_small_absolute_jump_is_clear():
    # +40%, but only 40 minutes more - not worth a warning.
    assert r.load_spike(None, None, 140, 100, T).status == r.CLEAR


def test_load_on_too_little_training_is_unknown():
    # Ratios on almost no training would warn about nothing.
    assert r.load_spike(60, 30, 80, 40, T).status == r.UNKNOWN


@pytest.mark.parametrize("acute,chronic", [(600, 400), (300, 400)])
def test_normal_load_is_clear(acute, chronic):
    assert r.load_spike(acute, chronic, 250, 230, T).status == r.CLEAR


def test_thresholds_are_respected():
    stricter = replace(T, rhr_delta=8)
    assert r.rhr_high(days(57, 58, 51), END, base(51), stricter).status == r.CLEAR
