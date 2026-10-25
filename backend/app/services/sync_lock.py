"""
At most one Garmin sync per user at a time.

Without this, pressing 🔄 while the scheduled 08:00 sync is already working
on the same person ran two syncs side by side: twice the Garmin requests
from one IP (exactly what gets it throttled), and two writers racing over
the same rows.

The lock lives in Redis rather than in the worker's memory because the
worker runs several processes - a lock in one process is invisible to the
others. It expires on its own, so a worker killed mid-sync cannot leave a
user locked out forever.
"""

import logging
from typing import Optional

import redis
from redis.exceptions import LockError

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Optional[redis.Redis] = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0)
    return _client


def user_sync_lock(user_id: int, timeout_seconds: int):
    """
    `timeout_seconds` must outlast the task's hard time limit: a lock that
    expires while its sync is still running would let a second one in.
    """
    return _redis().lock(f"garmin-sync-lock:{user_id}", timeout=timeout_seconds)


def named_lock(name: str, timeout_seconds: int):
    """A lock for one kind of job across all workers, e.g. the history import."""
    return _redis().lock(f"job-lock:{name}", timeout=timeout_seconds)


def release(lock) -> None:
    try:
        lock.release()
    except LockError:
        # Already expired - the time limit outran us. Nothing left to free.
        logger.warning("sync lock %s had already expired", lock.name)
