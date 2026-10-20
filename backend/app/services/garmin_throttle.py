"""
A shared "Garmin is throttling us, back off" switch.

Every user's requests leave from the same server IP, so when Garmin
rate-limits one of them it is effectively limiting all of them. Carrying on
with the other users' upload checks would only extend the block. The first
task to hit the limit sets this; every check skips until it expires.

In Redis, not in memory: the worker runs several processes, and a switch
only one of them can see stops nothing.
"""

import logging

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

KEY = "garmin-throttled"
BACKOFF_SECONDS = 30 * 60

_client = None


def _redis():
    global _client
    if _client is None:
        _client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0)
    return _client


def mark_throttled() -> None:
    try:
        _redis().set(KEY, "1", ex=BACKOFF_SECONDS)
        logger.warning("Garmin is throttling - pausing upload checks for %s min", BACKOFF_SECONDS // 60)
    except redis.RedisError:
        logger.warning("Could not set the Garmin throttle switch", exc_info=True)


def is_throttled() -> bool:
    try:
        return bool(_redis().exists(KEY))
    except redis.RedisError:
        # Unknown is treated as "not throttled": the sync's own retry and
        # backoff still apply, so the worst case is one extra request.
        return False
