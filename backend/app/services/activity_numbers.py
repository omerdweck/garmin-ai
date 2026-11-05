"""
One source for a workout's time and pace, so the chat, the coach and the
Mini App show exactly what the watch shows - Garmin's own numbers, not ours.

Time: Garmin's `duration`, the timer time, shown to the tenth like the watch
("35:29.8"). Not `movingDuration`: that also strips the moments you stood
still with the timer running, so it is shorter than what the watch shows.

Pace: Garmin's `averageSpeed` - the average the watch computed itself.
Only a manual entry (no Garmin data) falls back to time / distance.

Rounding is half-up, like Math.round in frontend/src/format.ts, so the
server and the page never disagree.
"""

import math
from typing import Optional


def _raw(activity) -> dict:
    return getattr(activity, "raw_json", None) or {}


def timer_seconds(activity) -> Optional[float]:
    return _raw(activity).get("duration") or activity.duration_seconds or None


def pace_seconds(activity, per_meters: float = 1000) -> Optional[float]:
    """Seconds per km (or per 100 m with per_meters=100), from Garmin's average speed."""
    speed = _raw(activity).get("averageSpeed")
    if speed and speed > 0:
        return per_meters / speed
    seconds, meters = timer_seconds(activity), activity.distance_meters
    if not seconds or not meters or meters < 100:
        return None
    return seconds / (meters / per_meters)


def mm_ss(seconds: Optional[float]) -> Optional[str]:
    """Whole seconds, for paces: 303.95 -> "5:04"."""
    if not seconds:
        return None
    total = math.floor(seconds + 0.5)
    hours, minutes, secs = total // 3600, (total % 3600) // 60, total % 60
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def watch_time(seconds: Optional[float]) -> Optional[str]:
    """A workout's total time to the tenth, as the watch shows it: 2129.84 -> "35:29.8"."""
    if not seconds:
        return None
    tenths = math.floor(seconds * 10)  # the watch cuts, it doesn't round up
    total, tenth = divmod(tenths, 10)
    hours, minutes, secs = total // 3600, (total % 3600) // 60, total % 60
    text = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
    return f"{text}.{tenth}"
