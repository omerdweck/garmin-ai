"""
The stored week starts on Sunday (index 0). Python's own weekday() starts on
Monday, so this conversion is the classic place for an off-by-one - and if
it slips, the daily summary tells the user the wrong workout for tomorrow.
"""

from datetime import date, datetime, timedelta

from app.models.plan_session import DAY_KEYS, week_index


def test_sunday_is_zero_and_saturday_is_six():
    assert week_index(date(2026, 10, 18)) == 0  # Sunday
    assert week_index(date(2026, 10, 19)) == 1  # Monday
    assert week_index(date(2026, 10, 24)) == 6  # Saturday


def test_index_matches_the_day_name_for_a_whole_week():
    # Checked against the calendar's own day name rather than a hand-typed
    # table, so the test cannot share a mistake with the code it checks.
    start = date(2026, 10, 18)
    for offset in range(7):
        day = start + timedelta(days=offset)
        assert DAY_KEYS[week_index(day)] == day.strftime("%A").lower()


def test_works_for_datetimes_as_well_as_dates():
    # dispatch_weekly_summaries passes a datetime, the summaries pass a date.
    assert week_index(datetime(2026, 10, 18, 23, 59)) == 0
