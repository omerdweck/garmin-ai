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
