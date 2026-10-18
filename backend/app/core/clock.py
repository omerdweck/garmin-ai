"""
The one place that answers "what time is it" and "what day is it".

Every user is in Israel, so every calendar question - which day a meal
belongs to, what "today" and "tomorrow" mean, which hour a summary goes out
- is answered in Israel time. Before this module those questions were
answered with date.today() and datetime.now(), which use the *server's* time
zone; the containers run in UTC, so for the first two or three hours after
midnight in Israel the bot still believed it was yesterday. A meal logged at
00:30 landed on the previous day, and the coach was told the wrong date.

Two conventions, deliberately different, and both fixed here:

  Calendar time (Israel) - "today", hours for reminders and summaries, and
      a workout's start_time, which is stored as the wall-clock time the
      user saw on their watch (Garmin's startTimeLocal), without a zone.

  Bookkeeping timestamps (UTC) - created_at, linked_at, last_sync_at and
      every other "when did this happen" column. UTC has no daylight-saving
      jumps, so it is the only zone where "how long ago" is always simple
      subtraction. Israel's clocks go back an hour in October: stored in
      local time, one hour of every year would happen twice.

Those UTC columns are `timestamp without time zone`, so the database must
read and write them in UTC regardless of how the server is configured -
app/db/session.py pins every connection to UTC for exactly that reason.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Jerusalem")


def _now_utc() -> datetime:
    # The single real clock read. Tests patch this to freeze time.
    return datetime.now(timezone.utc)


def now_utc() -> datetime:
    """For bookkeeping timestamps. Time-zone aware."""
    return _now_utc()


def now_local() -> datetime:
    """The current time in Israel. Time-zone aware."""
    return _now_utc().astimezone(LOCAL_TZ)


def now_local_naive() -> datetime:
    """
    Israel wall-clock time without a zone - the same shape as a Garmin
    workout's start_time, for workouts entered by hand.
    """
    return now_local().replace(tzinfo=None)


def today() -> date:
    """Today's date in Israel."""
    return now_local().date()


def utc_to_local_date(stamp: datetime) -> date:
    """
    The Israeli calendar date of a stored UTC timestamp. Naive values are
    UTC by the convention above; they come back from the database that way.
    """
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(LOCAL_TZ).date()
