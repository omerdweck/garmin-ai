"""
"Updated 12 minutes ago" and the "sync now" button in the Mini App.

The last successful pull from Garmin is already recorded per user:
sync_user_garmin_data sets GarminAccount.last_sync_at at the end of every
sync that worked, and the Mini App's read-only role may read that column.
"Is one running right now" is the per-user Redis lock every Garmin job
takes (app/services/sync_lock.py), so nothing new has to be stored for it.

Starting a sync from the page goes through the same task the bot's 🔄
button queues, so it inherits that task's guarantees: one sync per user at
a time (the lock), a retry with backoff when Garmin rate-limits. On top of
that the page is stricter than the bot, because a button on a page is
pressed more freely than a command in a chat:
  - nothing is queued while Garmin is throttling us (the shared switch in
    app/services/garmin_throttle.py) - every user's requests leave from
    the same IP, so one more request would only extend the block;
  - nothing is queued while a sync of this user's is running or queued;
  - at most one request per user per COOLDOWN_SECONDS, and none at all
    when the last sync is younger than that - the data could not be newer.

Keys and names are repeated here rather than imported from app/tasks.py:
the webapp process shouldn't load every task's dependencies to queue one
(the same choice as activity_page.request_detail).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import redis
from sqlmodel import Session, select

from app.core.config import settings
from app.models.garmin_account import GarminAccount

logger = logging.getLogger(__name__)

LOCK_KEY = "garmin-sync-lock:{}"  # sync_lock.user_sync_lock
THROTTLE_KEY = "garmin-throttled"  # garmin_throttle.KEY
# Set when the page queues a sync; holds the time it was queued. Between
# that moment and the worker taking the lock the sync is "running" from
# the user's point of view, though no lock exists yet.
REQUESTED_KEY = "miniapp-sync-requested:{}"
COOLDOWN_SECONDS = 5 * 60


def _redis() -> redis.Redis:
    return redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0, socket_connect_timeout=2)


def _account(session: Session, user_id: int):
    return session.exec(
        select(GarminAccount.last_sync_at, GarminAccount.disconnected_at).where(GarminAccount.user_id == user_id)
    ).first()


def _utc(stamp: Optional[datetime]) -> Optional[datetime]:
    if stamp is None:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _syncing(client: redis.Redis, user_id: int, last_sync: Optional[datetime]) -> bool:
    if client.exists(LOCK_KEY.format(user_id)):
        return True
    requested = client.get(REQUESTED_KEY.format(user_id))
    if requested is None:
        return False
    # Queued, and no sync has finished since: still on its way.
    return last_sync is None or float(requested) > last_sync.timestamp()


def status(session: Session, user_id: int) -> dict:
    row = _account(session, user_id)
    last_sync = _utc(row.last_sync_at) if row else None
    try:
        syncing = _syncing(_redis(), user_id, last_sync)
    except redis.RedisError:
        # A status line is not worth an error: say what we know.
        syncing = False
    return {
        "last_synced_at": last_sync.isoformat() if last_sync else None,
        "syncing": syncing,
        "linked": row is not None and row.disconnected_at is None,
    }


def request_sync(session: Session, user_id: int) -> str:
    """
    Returns what happened, for the page to say:
      started   - queued now
      syncing   - one is already running or queued; nothing new queued
      fresh     - the last sync is only minutes old
      busy      - Garmin is throttling us (or Redis is unreachable)
      unlinked  - no active Garmin account; the scheduled sync skips these too
    """
    row = _account(session, user_id)
    if row is None or row.disconnected_at is not None:
        return "unlinked"
    last_sync = _utc(row.last_sync_at)
    try:
        client = _redis()
        if client.exists(THROTTLE_KEY):
            return "busy"
        if _syncing(client, user_id, last_sync):
            return "syncing"
        if last_sync is not None and (datetime.now(timezone.utc) - last_sync).total_seconds() < COOLDOWN_SECONDS:
            return "fresh"
        # SET NX: two taps (or two open pages) queue one sync, not two.
        if not client.set(REQUESTED_KEY.format(user_id), str(time.time()), nx=True, ex=COOLDOWN_SECONDS):
            return "syncing"
        from app.celery_app import celery_app

        # notify_on_success left off: the page shows the result itself, and
        # a chat message on top of it would be the noise the scheduled run
        # avoids for the same reason.
        celery_app.send_task("app.tasks.sync_one_user_task", args=[user_id])
        return "started"
    except Exception:
        logger.warning("Could not queue a sync for user %s", user_id, exc_info=True)
        return "busy"
