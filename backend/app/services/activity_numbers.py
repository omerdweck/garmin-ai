"""
One source for a workout's time and pace, so the chat, the coach and the
Mini App show exactly what the watch shows.

The watch's "Time" is Garmin's `duration` - the timer time (stops only when
the timer is paused, manually or by Auto Pause). Its average pace is that
timer time over the distance. `movingDuration` is a different number: Garmin
also strips the moments you stood still with the timer running, so it is
shorter and makes the pace look faster than the watch does.

Seconds are rounded half-up, the same as Math.round in frontend/src/format.ts,
so the server and the page never disagree by a second.
"""

import math
from typing import Optional


def _round(seconds: float) -> int:
    return math.floor(seconds + 0.5)


def timer_seconds(activity) -> Optional[float]:
    return activity.duration_seconds or None


def pace_seconds(activity, per_meters: float = 1000) -> Optional[float]:
    """Seconds per km (or per 100 m with per_meters=100), timer time / distance."""
    seconds = timer_seconds(activity)
    meters = activity.distance_meters
    if not seconds or not meters or meters < 100:
        return None
    return seconds / (meters / per_meters)


def clock(seconds: Optional[float]) -> Optional[str]:
    """2129.8 -> "35:30", 3606 -> "1:00:06"."""
    if not seconds:
        return None
    total = _round(seconds)
    hours, minutes, secs = total // 3600, (total % 3600) // 60, total % 60
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
