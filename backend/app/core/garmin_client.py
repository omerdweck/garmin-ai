"""
Thin wrapper around the third-party `garminconnect` library - the rest
of the app only calls the functions below and never imports
`garminconnect` directly. It's an unofficial, reverse-engineered client;
if Garmin changes something and it breaks, this is the one file that
needs fixing, not every place that touches Garmin data.

Session persistence: garminconnect's own default (a token file under
~/.garminconnect/) doesn't fit us - tokens need to live per-user in our
own DB, encrypted. Instead we use client.client.dumps()/login(tokenstore=...)
to get/restore the session as a plain JSON string, which the router layer
encrypts (via app.core.crypto) before storing and decrypts before reuse.
This module never touches encryption or the DB - it only knows about Garmin.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from garminconnect import Garmin
from garminconnect.exceptions import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GarminAuthError",
    "GarminRateLimitError",
    "login_to_garmin",
    "resume_garmin_session",
    "get_daily_summary",
    "get_sleep_data",
    "get_hrv_data",
    "get_max_metrics",
    "get_activities",
    "get_exercise_sets",
    "get_watch_last_upload",
    "get_training_load",
    "get_race_predictions",
    "get_personal_records",
]


class GarminAuthError(Exception):
    """Raised when Garmin rejects the credentials or an existing session token."""


class GarminRateLimitError(Exception):
    """Raised when Garmin itself is throttling/blocking login attempts (HTTP 429) - not a credentials problem."""


def login_to_garmin(email: str, password: str) -> str:
    """
    Logs into Garmin with the user's credentials and returns an opaque
    JSON string bundling the session tokens. Callers must encrypt and
    store this string themselves - the password is never touched again
    once this call returns (login() sends it once, over HTTPS, to Garmin).
    """
    try:
        # retry_attempts=1: don't amplify load against Garmin on repeated
        # failures (bad credentials or a real outage won't fix themselves
        # by retrying immediately, and hammering their login endpoint is
        # exactly what triggers their rate limiting).
        client = Garmin(email=email, password=password, retry_attempts=1)
        client.login()
    except (GarminConnectTooManyRequestsError, GarminConnectConnectionError) as exc:
        # GarminConnectConnectionError also covers Cloudflare bot-challenge
        # blocks (HTTP 403), not just real network errors - that's Garmin
        # throttling us, not proof the credentials were checked and
        # rejected. Only a genuine GarminConnectAuthenticationError means
        # "these credentials were actually rejected".
        raise GarminRateLimitError(str(exc)) from exc
    except GarminConnectAuthenticationError as exc:
        raise GarminAuthError(str(exc)) from exc

    return client.client.dumps()


def resume_garmin_session(token_bundle: str) -> Garmin:
    """
    Restores a previously saved session from a token bundle (as returned
    by login_to_garmin) - no password involved. Garmin's client library
    auto-refreshes the underlying token if it's close to expiring.
    """
    try:
        client = Garmin(retry_attempts=1)
        client.login(tokenstore=token_bundle)
    except (GarminConnectTooManyRequestsError, GarminConnectConnectionError) as exc:
        # GarminConnectConnectionError also covers Cloudflare bot-challenge
        # blocks (HTTP 403), not just real network errors - that's Garmin
        # throttling us, not proof the credentials were checked and
        # rejected. Only a genuine GarminConnectAuthenticationError means
        # "these credentials were actually rejected".
        raise GarminRateLimitError(str(exc)) from exc
    except GarminConnectAuthenticationError as exc:
        raise GarminAuthError(str(exc)) from exc

    return client


def get_daily_summary(session: Garmin, date: str) -> dict:
    """date is 'YYYY-MM-DD'. Steps, calories, HR, stress, body battery for one day."""
    return session.get_stats(date)


def get_sleep_data(session: Garmin, date: str) -> dict:
    """date is 'YYYY-MM-DD'. The stage breakdown lives under the 'dailySleepDTO' key."""
    return session.get_sleep_data(date)


def get_hrv_data(session: Garmin, date: str) -> dict:
    """
    date is 'YYYY-MM-DD'. The daily-level values live under 'hrvSummary'
    (lastNightAvg, status); 'hrvReadings' is a list of many readings
    through the night - intraday time-series we deliberately don't turn
    into structured columns (see DailyMetric's docstring).
    """
    return session.get_hrv_data(date)


def get_max_metrics(session: Garmin, date: str) -> list[dict]:
    """
    date is 'YYYY-MM-DD'. VO2 max / fitness age. Returns an empty list on
    days where Garmin didn't recalculate these (no qualifying activity),
    which is most days - callers must handle that, not assume a value.
    """
    return session.get_max_metrics(date)


def get_activities(session: Garmin, start: int = 0, limit: int = 20) -> list[dict]:
    """Most recent activities first. start/limit are for pagination through history."""
    return session.get_activities(start, limit)


def _newest_device_entry(device_map: dict) -> dict:
    """
    Garmin keys these payloads by device id, because a person can own more
    than one watch. Pick the most recently written entry rather than an
    arbitrary one, so a second device that synced days ago cannot shadow the
    watch actually in use.
    """
    if not device_map:
        return {}
    return max(device_map.values(), key=lambda d: (d or {}).get("timestamp") or 0)


def get_training_load(session: Garmin, date: str) -> dict:
    """
    Garmin's own training-load and readiness figures for one day.

    Read rather than computed. An acute:chronic ratio derived from our stored
    activity durations would be a worse version of a number Garmin already
    produces from sensor data we never see, using algorithms with years of
    validation behind them.

    Verified to be genuinely per-date, not "most recent" repeated: acute load
    varies day to day and readiness varies with it. Chronic load looks static
    within a month because it is a 28-day average - it does move across a
    longer span. That mattered before writing this, because an endpoint that
    quietly returned today's value for every date would have stamped one
    number across a whole backfill and called it history.

    Returns {} on any failure, and never raises. This is supplementary
    context; losing it must not fail a sync that is otherwise fine - including
    when Garmin throttles these two extra calls but not the core ones.
    """
    try:
        result: dict = {}

        status = session.get_training_status(date) or {}

        recent = (status.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
        device = _newest_device_entry(recent)
        load = device.get("acuteTrainingLoadDTO") or {}
        result["acute_load"] = load.get("dailyTrainingLoadAcute")
        result["chronic_load"] = load.get("dailyTrainingLoadChronic")
        # The phrase, not the numeric trainingStatus: "RECOVERY_2" carries
        # meaning a bare 5 does not, and it is what Claude can reason about.
        result["training_status"] = device.get("trainingStatusFeedbackPhrase")

        balance = (status.get("mostRecentTrainingLoadBalance") or {}).get(
            "metricsTrainingLoadBalanceDTOMap"
        ) or {}
        result["load_balance"] = _newest_device_entry(balance).get("trainingBalanceFeedbackPhrase")

        # A list of snapshots taken through the day, so the last one written
        # is the current state - taking [0] would depend on an ordering
        # Garmin never promised.
        readiness = session.get_training_readiness(date) or []
        if isinstance(readiness, list) and readiness:
            latest = max(readiness, key=lambda r: (r or {}).get("timestamp") or "")
            result["readiness_score"] = latest.get("score")
            result["readiness_level"] = latest.get("level")
            result["recovery_time_minutes"] = latest.get("recoveryTime")

        return result
    except Exception:
        logger.debug("could not read training load for %s", date, exc_info=True)
        return {}


def get_race_predictions(session: Garmin, start: str, end: str) -> dict:
    """
    Garmin's predicted finish times for 5K, 10K, half and full marathon,
    keyed by calendar date, in seconds.

    Fetched as one ranged call for the whole sync window rather than one per
    day: the endpoint accepts a range, and turning a 30-day backfill into 30
    extra requests against an IP-rate-limited API to get data one call
    already returns would be indefensible.

    Worth storing per day rather than only the latest: the prediction moves
    as fitness moves, so the history is the progress measure. A prediction
    that has drifted a minute slower over two months says something no
    single day's figure can.

    Returns {} on any failure - supplementary context must never fail a sync.
    """
    try:
        rows = session.get_race_predictions(startdate=start, enddate=end, _type="daily")
        if not isinstance(rows, list):
            return {}
        return {
            row["calendarDate"]: {
                "5k": row.get("time5K"),
                "10k": row.get("time10K"),
                "half": row.get("timeHalfMarathon"),
                "marathon": row.get("timeMarathon"),
            }
            for row in rows
            if row.get("calendarDate")
        }
    except Exception:
        logger.debug("could not read race predictions", exc_info=True)
        return {}


def get_personal_records(session: Garmin) -> list:
    """
    Garmin's current personal records. No date argument - these are "the best
    ever", not a per-day figure.

    Returns [] on failure like the other supplementary readers: a missing
    record list must not fail a sync that otherwise worked.
    """
    try:
        records = session.get_personal_record()
        return records if isinstance(records, list) else []
    except Exception:
        logger.debug("could not read personal records", exc_info=True)
        return []


def get_watch_last_upload(session: Garmin) -> Optional[datetime]:
    """
    When the user's watch last uploaded to Garmin Connect, as an aware UTC
    datetime, or None if Garmin doesn't say.

    This is the answer to "why is my data stale?". Everything else here reads
    what Garmin already holds, and Garmin only holds what the watch has
    uploaded - so a watch that hasn't synced in two days makes our sync look
    broken when it is working perfectly.

    Deliberately swallows every exception: this is supplementary context, and
    a failure to fetch it must never take down a sync that is otherwise fine.
    """
    try:
        info = session.get_device_last_used() or {}
        # Garmin returns epoch milliseconds, UTC.
        millis = info.get("lastUsedDeviceUploadTime")
        if not millis:
            return None
        return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
    except Exception:
        logger.debug("could not read last device upload time", exc_info=True)
        return None


def get_exercise_sets(session: Garmin, activity_id: int) -> dict:
    """
    Per-set detail for a strength workout: each set's detected exercise
    category, rep count and weight. Only meaningful for strength training -
    other activity types return an empty set list.
    """
    return session.get_activity_exercise_sets(activity_id)
