"""
Time handling: Israel for calendar questions, UTC for bookkeeping.

Most of these freeze the clock just after midnight in Israel - the window
where the server (UTC) and the user disagree about what day it is, and
where every date.today() in the old code was wrong.
"""

import re
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlmodel import select

from app.core import clock
from app.models.garmin_account import GarminAccount
from app.services.calorie_tracking import add_entry


def _frozen_at(utc: datetime):
    return patch("app.core.clock._now_utc", return_value=utc)


@pytest.mark.parametrize(
    "utc, israeli_date",
    [
        # 22:30 UTC on 18 Oct is already 19 Oct in Israel (summer time, +3).
        (datetime(2026, 10, 18, 22, 30, tzinfo=timezone.utc), date(2026, 10, 19)),
        # Winter, +2: 22:30 UTC is 00:30 the next day.
        (datetime(2026, 1, 15, 22, 30, tzinfo=timezone.utc), date(2026, 1, 16)),
        # Summer, +3: 21:30 UTC is 00:30 the next day. With a fixed +2 offset
        # this would wrongly still be 1 July - so this case checks that
        # daylight saving is honoured, not just a constant shift.
        (datetime(2026, 7, 1, 21, 30, tzinfo=timezone.utc), date(2026, 7, 2)),
        # Midday: both agree.
        (datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc), date(2026, 7, 1)),
    ],
)
def test_today_is_the_israeli_date(utc, israeli_date):
    with _frozen_at(utc):
        assert clock.today() == israeli_date


def test_local_naive_is_israeli_wall_clock_without_a_zone():
    with _frozen_at(datetime(2026, 7, 1, 21, 30, tzinfo=timezone.utc)):
        local = clock.now_local_naive()

    assert local.tzinfo is None
    assert (local.date(), local.hour, local.minute) == (date(2026, 7, 2), 0, 30)


def test_stored_utc_timestamp_maps_to_israeli_date():
    # Naive, the way it comes back from the database.
    assert clock.utc_to_local_date(datetime(2026, 10, 18, 22, 30)) == date(2026, 10, 19)


def test_meal_logged_after_midnight_lands_on_the_new_day(session, make_user):
    # The bug this module exists for: at 00:30 in Israel the server's UTC
    # date was still yesterday, so the meal was filed under the wrong day.
    me = make_user()
    with _frozen_at(datetime(2026, 10, 18, 21, 30, tzinfo=timezone.utc)):  # 00:30 IL
        entry = add_entry(session, me.id, 500)

    assert entry.entry_date == date(2026, 10, 19)


def test_database_connections_use_utc(session):
    # Without the pin in app/db/session.py this returns the server's own
    # zone - on a Postgres installed in Israel, 'Asia/Jerusalem'.
    assert session.exec(text("SHOW timezone")).one()[0] == "UTC"


def test_utc_timestamp_survives_a_round_trip(session, make_user):
    me = make_user()
    stamp = datetime(2026, 10, 18, 22, 30, tzinfo=timezone.utc)

    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == me.id)).one()
    account.watch_last_upload_at = stamp
    session.add(account)
    session.commit()
    session.expire_all()

    stored = session.exec(select(GarminAccount).where(GarminAccount.user_id == me.id)).one()
    assert stored.watch_last_upload_at.replace(tzinfo=timezone.utc) == stamp


APP = Path(__file__).resolve().parent.parent / "app"
# Reading the clock any other way uses the server's zone, which is UTC in
# production - exactly the bug clock.py removed. Comments are skipped so the
# explanation in clock.py itself does not count.
FORBIDDEN = re.compile(r"\b(date|date_type)\.today\(\)|\bdatetime\.now\(\)|\butcnow\(\)")


def test_nothing_reads_the_server_clock_directly():
    offenders = []
    for path in APP.rglob("*.py"):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if FORBIDDEN.search(code) and path.name != "clock.py":
                offenders.append(f"{path.relative_to(APP.parent)}:{number}: {line.strip()}")

    assert offenders == [], "use app.core.clock instead:\n" + "\n".join(offenders)
