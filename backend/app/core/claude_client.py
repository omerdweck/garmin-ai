"""
The AI coach: wraps the Claude Messages API with tool use, so Claude can
pull the user's real Garmin data instead of being handed a dump of it.

Why tools rather than stuffing data into the prompt: the alternative is
sending every metric and activity on every message, which costs tokens on
every "thanks!" and grows without bound as history accumulates. With
tools, Claude asks for exactly the slice each question needs.

The tool loop here is manual (rather than the SDK's beta tool_runner)
because each tool call needs the caller's DB session and user_id, and
because the loop is short and worth being able to read end to end.

Every tool is scoped to one user_id, bound from the caller - the model
never supplies or influences which user's data is read. That's what makes
this safe to run for many users on one bot.
"""

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic
from sqlmodel import Session, col, delete, select

from app.core import clock
from app.core.claude_pricing import calculate_cost
from app.services.calorie_tracking import (
    burned_on,
    consumed_on,
    is_burn_provisional,
)
from app.core.claude_prompts import (
    ANOMALY_SYSTEM_PROMPT,
    COACH_SYSTEM_PROMPT,
    POST_WORKOUT_SYSTEM_PROMPT,
    DAILY_SUMMARY_SYSTEM_PROMPT,
    WEEKLY_SUMMARY_SYSTEM_PROMPT,
)
from app.core.config import settings
from app.models.activity import SOURCE_MANUAL, Activity
from app.models.chat_message import ChatMessage
from app.models.daily_metric import DailyMetric
from app.models.meal_plan import MEAL_SLOTS, MealOption, MealPlan
from app.models.personal_record import PersonalRecord
from app.models.plan_session import DAY_KEYS, DAY_NAMES_HE, PlanSession, week_index
from app.models.training_plan import TrainingPlan
from app.models.usage_event import UsageEvent
from app.services.anomalies import health_overview
from app.services.athlete_profile import add_note, profile_as_dict, profile_prompt_block, update_profile
from app.services.records import format_value, record_meta
from app.services.weekly_report import build_weekly_facts, format_weekly_facts
from app.models.user import User

logger = logging.getLogger(__name__)

# How many past chat turns to replay as context. Enough to hold a thread
# ("what about last week?"), small enough that cost stays flat over time.
CHAT_HISTORY_LIMIT = 20

# Safety valve on the tool loop - a well-behaved turn uses 1-2 rounds.
MAX_TOOL_ROUNDS = 6


class ClaudeNotConfiguredError(RuntimeError):
    """Raised when the coach is used before ANTHROPIC_API_KEY is set."""


class ClaudeOutOfCreditError(RuntimeError):
    """Raised when the Anthropic account has no credit left."""


class ClaudeAuthError(RuntimeError):
    """Raised when the API key is rejected - wrong, revoked or expired."""


class ClaudeUnavailableError(RuntimeError):
    """Raised for transient problems: network, rate limits, Anthropic outages."""


# A ceiling on one API call. The SDK's own default is ten minutes, which is
# ten minutes of a coach thread (and a database connection) held by a request
# that is not coming back. A real answer arrives far inside this.
CLAUDE_TIMEOUT_SECONDS = 120.0

_shared_client: Optional[anthropic.Anthropic] = None
_shared_client_lock = threading.Lock()


def _client() -> anthropic.Anthropic:
    """
    One client for the whole process, created on first use.

    A new client per call opened a new HTTPS connection - TLS handshake and
    all - for every call, including each extra round of a tool-using turn.
    The shared one keeps connections open and reuses them. The SDK client is
    safe to share between threads, which matters because the coach runs on
    several at once.
    """
    global _shared_client
    if not settings.is_claude_configured:
        raise ClaudeNotConfiguredError("ANTHROPIC_API_KEY is not set")
    if _shared_client is None:
        with _shared_client_lock:
            if _shared_client is None:  # another thread may have won the race
                _shared_client = anthropic.Anthropic(
                    api_key=settings.anthropic_api_key,
                    timeout=CLAUDE_TIMEOUT_SECONDS,
                )
    return _shared_client


def _record_usage(session: Session, user_id: int, kind: str, model: str, usage) -> None:
    """
    Persist what one API call consumed.

    Wrapped so it can never break a conversation: an accounting failure must
    not cost the user their reply. The consequence of a swallowed error here
    is an under-reported cost, which the warning in the log makes visible.

    The cache counters are read defensively - they are absent on responses
    that used no caching, and getattr keeps this working if the SDK renames
    or drops them.
    """
    try:
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        event = UsageEvent(
            user_id=user_id,
            kind=kind,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=calculate_cost(
                model,
                usage.input_tokens,
                usage.output_tokens,
                cache_read,
                cache_write,
            ),
        )
        session.add(event)
        session.commit()
    except Exception:
        logger.exception("Failed to record usage for user %s", user_id)
        session.rollback()


def _call_claude(
    client: anthropic.Anthropic,
    *,
    session: Optional[Session] = None,
    user_id: Optional[int] = None,
    kind: str = "chat",
    **kwargs,
):
    """
    Single place where the API is actually called, so every failure mode
    gets classified once instead of surfacing to the user as one generic
    "something went wrong".

    The distinction matters because the fixes are completely different:
    out of credit is something the *owner* has to top up, a bad key is a
    deployment problem, and a network blip just needs retrying. Telling
    the user which one it is saves them guessing.

    Being the one choke point, this is also where usage is metered - every
    call is counted exactly once, including the extra rounds of a tool-use
    loop, which a caller-side counter would have missed.
    """
    try:
        response = client.messages.create(**kwargs)
        if session is not None and user_id is not None:
            _record_usage(session, user_id, kind, kwargs.get("model", "unknown"), response.usage)
        return response
    except anthropic.AuthenticationError as exc:
        raise ClaudeAuthError(str(exc)) from exc
    except anthropic.PermissionDeniedError as exc:
        raise ClaudeAuthError(str(exc)) from exc
    except anthropic.BadRequestError as exc:
        # Credit exhaustion arrives as a 400, not a dedicated exception
        # type - the message is the only thing distinguishing it from a
        # genuinely malformed request.
        if "credit balance" in str(exc).lower():
            raise ClaudeOutOfCreditError(str(exc)) from exc
        raise
    except anthropic.RateLimitError as exc:
        raise ClaudeUnavailableError(str(exc)) from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeUnavailableError(str(exc)) from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise ClaudeUnavailableError(str(exc)) from exc
        raise


# --------------------------------------------------------------------------
# Tool definitions (the schemas Claude sees) and their implementations
# --------------------------------------------------------------------------

TOOLS = [
    {
        "name": "get_recent_daily_metrics",
        "description": (
            "Fetches the user's daily health metrics from their Garmin watch: sleep duration and "
            "stages, resting/min/max heart rate, HRV, VO2 max, stress, Body Battery, steps, "
            "calories and intensity minutes. Returns one entry per day, most recent first. "
            "Days the watch wasn't worn are missing or have null fields. Use this for any question "
            "about how the user is feeling, recovering, sleeping, or trending.\n\n"
            "Also includes Garmin's own training-load figures, which are the best answer to 'am I "
            "doing too much / too little': training_load_acute_7d against training_load_chronic_28d "
            "(acute well below chronic means detraining, well above means a hard block), plus "
            "training_status, load_balance, readiness_score (0-100), readiness_level and "
            "recovery_time_minutes. Always prefer these over counting workouts yourself - they come "
            "from sensor data and are calibrated to this individual.\n\n"
            "Also includes Garmin's predicted race finish times in seconds "
            "(race_predict_5k_seconds, _10k_, _half_, _marathon_). These are the most direct answer "
            "to 'am I on track for my goal' - compare the prediction against a stated target time. "
            "They are estimates from current fitness, never measured results, and must be described "
            "as such. Their movement over weeks is a real progress signal: a 10K prediction drifting "
            "a minute slower means fitness fell, not that one day went badly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days back to fetch (1-60). Use 7 for 'this week', 14 for trends, 1-2 for 'today'.",
                }
            },
            "required": ["days"],
        },
    },
    {
        "name": "get_recent_activities",
        "description": (
            "Fetches the user's recorded workouts (runs, rides, swims, strength sessions) with "
            "type, start time, duration, distance, average/max heart rate and calories. Most "
            "recent first. Use this for any question about training, workouts, pace, or volume."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many recent workouts to fetch (1-30).",
                }
            },
            "required": ["limit"],
        },
    },
    {
        "name": "get_training_goal",
        "description": (
            "Returns the training goal the user previously told the coach, or null if none is set. "
            "Check this before proposing a training plan so advice matches what they're working toward."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_training_goal",
        "description": (
            "Saves or updates the user's training goal so it persists across conversations. Call "
            "this whenever the user states what they're training for (a race, a distance, a pace, "
            "a date, losing weight, general fitness)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The goal in the user's own words, e.g. 'לרוץ 10 ק\"מ מתחת ל-50 דקות עד נובמבר'.",
                }
            },
            "required": ["goal"],
        },
    },
    {
        "name": "get_training_plans",
        "description": (
            "Returns every training plan previously prescribed to this user, one per discipline, "
            "each with the date it was written. Call this before answering anything about progress, "
            "adherence, or 'the plan you gave me' - the chat history only holds the last few turns, "
            "so an older plan is not in context. Never describe a plan from memory: if this returns "
            "nothing for a discipline, say so instead of inventing one."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_training_plan",
        "description": (
            "Saves the training plan for ONE discipline so it survives beyond this conversation. "
            "Call this whenever you prescribe a concrete plan. Saving a discipline replaces only "
            "that discipline's plan and leaves the others alone, so a user can have a running plan "
            "and a swimming plan at the same time. To revise an existing plan, call this again with "
            "the same discipline and the full updated plan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "discipline": {
                    "type": "string",
                    # A closed list, not free text: the same sport written two
                    # different ways would create two rows and the user would
                    # end up with duplicate, conflicting plans.
                    "enum": ["running", "swimming", "cycling", "strength", "general"],
                    "description": "Which discipline this plan covers. Use 'general' for overall fitness or mixed training.",
                },
                "plan": {
                    "type": "string",
                    "description": (
                        "The reasoning behind the plan in Hebrew, self-contained enough to be "
                        "understood weeks later without the surrounding conversation: what it is "
                        "building toward, the overall approach, and how it should progress."
                    ),
                },
                "sessions": {
                    "type": "array",
                    "description": (
                        "The week, one entry per training day AND per rest day. Required whenever "
                        "you prescribe an actual schedule. This is what lets the daily summary tell "
                        "the user what they have tomorrow, so a plan without it cannot be followed "
                        "day to day. Include rest days explicitly - an absent day is ambiguous."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "day": {
                                "type": "string",
                                "enum": [
                                    "sunday", "monday", "tuesday", "wednesday",
                                    "thursday", "friday", "saturday",
                                ],
                            },
                            "title": {
                                "type": "string",
                                "description": "Short name in Hebrew, e.g. 'ריצה קלה', 'אינטרוולים', 'מנוחה'.",
                            },
                            "details": {
                                "type": "string",
                                "description": (
                                    "Exactly what to do, in Hebrew. For intervals give the full set "
                                    "(warm-up, reps, recovery, cool-down). Someone reading this alone, "
                                    "weeks later, must know what to do without asking."
                                ),
                            },
                            "is_rest": {"type": "boolean"},
                            "target_distance_km": {"type": "number"},
                            "target_duration_minutes": {"type": "integer"},
                            "target_pace": {
                                "type": "string",
                                "description": "Free text - a range or a feel is fine, e.g. '5:30-5:45/ק\"מ' or 'קצב שיחה'.",
                            },
                        },
                        "required": ["day", "title"],
                    },
                },
            },
            "required": ["discipline", "plan"],
        },
    },
    {
        "name": "delete_training_plan",
        "description": (
            "Removes the plan for one discipline - use when the user says they are stopping that "
            "training, or the event it was built for has passed. Do not use it to replace a plan; "
            "set_training_plan already overwrites."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "discipline": {
                    "type": "string",
                    "enum": ["running", "swimming", "cycling", "strength", "general"],
                }
            },
            "required": ["discipline"],
        },
    },
    {
        "name": "get_personal_records",
        "description": (
            "Returns the user's personal records from Garmin, per sport. Call this when they ask "
            "about their bests, when setting a goal or a plan that involves a target time or "
            "distance, or when judging whether a result was strong for them - a 24-minute 5 km "
            "means one thing for someone whose record is 21:50 and another for someone at 30:00.\n\n"
            "Each record has `value_text` already formatted for display, plus `value` with its "
            "`unit` ('time' in seconds, 'distance' in metres) and `lower_is_better` so you can "
            "compare correctly - a faster time is a SMALLER number, a longer distance a BIGGER "
            "one. `achieved_at` is when it was set; a record from years ago is context worth "
            "using, not a current fitness level.\n\n"
            "Two things not to get wrong. These are Garmin's records, which include the best "
            "segment INSIDE a longer workout - a 5 km record may come from within a 10 km run, so "
            "never describe it as a race the user ran. And the list covers only the record types "
            "Garmin labels reliably: a sport or distance missing from it means we have no record "
            "for it, NOT that the user has never done it. Never invent a record that is not here."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_calorie_status",
        "description": (
            "Returns the user's calorie tracking state: whether it is on at all, their daily "
            "target, and the last few days of intake against what Garmin measured they burned. "
            "`tracking: false` means the user has not opted in - say nothing about calories in "
            "that case and do not push them to start unless they raise it.\n\n"
            "For each day: `consumed` is what the user logged by hand, `burned` is Garmin's TOTAL "
            "daily expenditure (BMR included, not just workouts), and `burn_source` says where it "
            "came from - 'garmin' measured, 'manual' the user typed it, 'none' means the watch was "
            "not worn. When burn_source is 'none' there is NO balance for that day; say so instead "
            "of inventing one. `burn_provisional` on today means the figure is still accumulating."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days back (1-30). Use 1 for today, 7 for a week's trend.",
                }
            },
        },
    },
    {
        "name": "get_meal_plan",
        "description": (
            "Returns the user's saved meal plan: the calorie target it was built for, the "
            "reasoning behind it, and the options for each meal slot. Call this before answering "
            "anything about what they should eat or how the plan is going - chat history holds "
            "only the last few turns, so a plan from last week is not in front of you. Never "
            "describe a plan you cannot see."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_meal_plan",
        "description": (
            "Saves or replaces the user's meal plan. Call this whenever you build one. Give "
            "2-3 options per meal so there is real variety - a single fixed menu is one nobody "
            "follows past the third day. The options should add up to roughly the target. "
            "Saving replaces the whole plan, so send every meal every time, not just the changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_calories": {
                    "type": "integer",
                    "description": "The daily intake the options add up to.",
                },
                "approach": {
                    "type": "string",
                    "description": (
                        "In Hebrew: what the plan is built around and why - protein split, meal "
                        "timing around training, anything the user asked for. Self-contained "
                        "enough to make sense weeks later without this conversation."
                    ),
                },
                "meals": {
                    "type": "array",
                    "description": "Every option, across all slots.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slot": {
                                "type": "string",
                                "enum": ["breakfast", "lunch", "dinner", "snack"],
                            },
                            "title": {
                                "type": "string",
                                "description": "Short name in Hebrew, e.g. 'חביתה עם אבוקדו'.",
                            },
                            "description": {
                                "type": "string",
                                "description": "Portions and ingredients, in Hebrew, concrete enough to actually make.",
                            },
                            "calories": {"type": "integer"},
                        },
                        "required": ["slot", "title"],
                    },
                },
            },
            "required": ["target_calories", "approach", "meals"],
        },
    },
    {
        "name": "delete_meal_plan",
        "description": (
            "Removes the meal plan entirely. Use only when the user asks to stop - "
            "set_meal_plan already replaces an existing plan."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_athlete_profile",
        "description": (
            "Returns the athlete's long-term profile (sports, experience, which days they can "
            "train, session length, equipment, pool length, FTP, limitations) and the notes you "
            "saved earlier with `remember`. A compact copy is already in your instructions; call "
            "this when you need the note ids or want to be sure you have the latest."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_health_alerts",
        "description": (
            "Returns what is normal for this athlete (28-day mean and spread of resting HR, HRV, "
            "sleep, Body Battery and easy-run efficiency in metres per heartbeat) and any recent "
            "anomaly alerts measured against that normal - open ones and ones that ended in the "
            "last 14 days. Use it when the athlete asks about an alert, about fatigue or "
            "recovery, or before suggesting harder training. Never present an alert as a diagnosis."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_athlete_profile",
        "description": (
            "Saves facts the athlete has TOLD you about themselves, so you never have to ask "
            "again. Send only the fields that changed. Never store something you concluded or "
            "guessed - only what they said. Values outside the allowed ones are rejected and "
            "reported back to you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sports": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["running", "cycling", "swimming", "strength", "walking",
                                 "hiking", "hiit", "yoga", "mobility", "other"],
                    },
                },
                "experience": {"type": "string", "enum": ["beginner", "intermediate", "advanced"]},
                "training_days": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["sunday", "monday", "tuesday", "wednesday",
                                 "thursday", "friday", "saturday"],
                    },
                    "description": "The days they CAN train, not the days a plan uses.",
                },
                "max_session_minutes": {"type": "integer"},
                "equipment": {"type": "string", "description": "In their words, e.g. 'חדר כושר מלא', 'משקולות בבית'."},
                "pool_length_m": {"type": "integer"},
                "ftp_watts": {"type": "integer"},
                "limitations": {
                    "type": "string",
                    "description": "Injuries or limits exactly as they described them. Never a diagnosis.",
                },
            },
        },
    },
    {
        "name": "remember",
        "description": (
            "Saves one short fact the athlete told you that matters for future coaching and "
            "does not fit the profile fields - a preference, a constraint, an event. Example: "
            "'מעדיף לרוץ בבוקר לפני העבודה'. One fact per call, in Hebrew, under 300 characters. "
            "Never save your own conclusions or anything they did not say. The athlete can see "
            "and delete every note."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": ["note"],
        },
    },
]


def _metric_to_dict(metric: DailyMetric) -> dict:
    """Compact projection - only fields worth spending context tokens on."""
    fields = {
        "date": metric.date.isoformat(),
        "sleep_minutes": metric.sleep_duration_minutes,
        "deep_sleep_minutes": metric.deep_sleep_minutes,
        "rem_sleep_minutes": metric.rem_sleep_minutes,
        "sleep_score": metric.sleep_score,
        "sleep_score_qualifier": metric.sleep_score_qualifier,
        # Absent unless Pulse Ox during sleep is on in the watch.
        "avg_spo2": metric.avg_spo2,
        "lowest_spo2": metric.lowest_spo2,
        "sleep_respiration_avg": metric.avg_sleep_respiration,
        "sleep_respiration_lowest": metric.lowest_sleep_respiration,
        "restless_moments": metric.restless_moments,
        "body_battery_charged_overnight": metric.sleep_body_battery_change,
        "bedtime": metric.sleep_start_local.strftime("%H:%M") if metric.sleep_start_local else None,
        "wake_time": metric.sleep_end_local.strftime("%H:%M") if metric.sleep_end_local else None,
        "resting_heart_rate": metric.resting_heart_rate,
        "max_heart_rate": metric.max_heart_rate,
        "hrv": metric.avg_hrv,
        "hrv_status": metric.hrv_status,
        "vo2_max": metric.vo2_max,
        "steps": metric.steps,
        "active_calories": metric.active_calories,
        # Total daily expenditure, BMR included. Never sent before, and
        # without it the coach cannot reason about a calorie balance at all.
        "total_calories": metric.total_calories,
        "distance_meters": metric.distance_meters,
        "avg_stress": metric.avg_stress_level,
        "body_battery_high": metric.body_battery_high,
        "body_battery_low": metric.body_battery_low,
        "intensity_minutes": (metric.moderate_intensity_minutes or 0) + (metric.vigorous_intensity_minutes or 0),
        # Garmin's own load figures. acute is ~7 days, chronic ~28, and the
        # ratio between them is what makes a week heavy or light *for this
        # person* - the same 300 means overreaching for one user and a taper
        # for another, which raw distance and duration cannot express.
        "training_load_acute_7d": metric.training_load_acute,
        "training_load_chronic_28d": metric.training_load_chronic,
        "training_status": metric.training_status,
        "load_balance": metric.load_balance,
        "readiness_score": metric.training_readiness_score,
        "readiness_level": metric.training_readiness_level,
        "recovery_time_minutes": metric.recovery_time_minutes,
        # Garmin's predicted finish times in seconds. The most direct answer
        # to "am I on track for my goal" the data contains.
        "race_predict_5k_seconds": metric.race_predict_5k_seconds,
        "race_predict_10k_seconds": metric.race_predict_10k_seconds,
        "race_predict_half_seconds": metric.race_predict_half_seconds,
        "race_predict_marathon_seconds": metric.race_predict_marathon_seconds,
    }
    # Dropping nulls keeps tool results small and stops the model from
    # reading "sleep_minutes: null" as "slept 0 hours".
    return {key: value for key, value in fields.items() if value is not None}


def _activity_to_dict(activity: Activity) -> dict:
    result = {
        "type": activity.activity_type,
        "name": activity.activity_name,
        # Only sent for manual rows - a "source": "garmin" on every activity
        # would be tokens spent restating the default. Its presence is the
        # signal that the missing fields below are missing by nature.
        "manually_entered": True if activity.source == SOURCE_MANUAL else None,
        "start_time": activity.start_time.isoformat(),
        "duration_minutes": round(activity.duration_seconds / 60, 1) if activity.duration_seconds else None,
        "distance_meters": activity.distance_meters,
        "avg_heart_rate": activity.avg_heart_rate,
        "max_heart_rate": activity.max_heart_rate,
        "calories": activity.calories,
    }
    if activity.distance_meters and activity.duration_seconds and activity.distance_meters > 100:
        pace = activity.duration_seconds / (activity.distance_meters / 1000)
        result["pace_per_km"] = f"{int(pace // 60)}:{int(pace % 60):02d}"
    return {key: value for key, value in result.items() if value is not None}


def fetch_daily_metrics(session: Session, user_id: int, days: int) -> list[dict]:
    days = max(1, min(days, 60))
    cutoff = clock.today() - timedelta(days=days)
    metrics = session.exec(
        select(DailyMetric)
        .where(DailyMetric.user_id == user_id, DailyMetric.date >= cutoff)
        .order_by(col(DailyMetric.date).desc())
    ).all()
    return [_metric_to_dict(metric) for metric in metrics]


def fetch_activities(session: Session, user_id: int, limit: int) -> list[dict]:
    limit = max(1, min(limit, 30))
    activities = session.exec(
        select(Activity)
        .where(Activity.user_id == user_id)
        .order_by(col(Activity.start_time).desc())
        .limit(limit)
    ).all()
    return [_activity_to_dict(activity) for activity in activities]


def fetch_training_plans(session: Session, user_id: int) -> list[dict]:
    """
    Plans with their weekly schedule attached, shared by the coach tool and
    the daily summary so both see exactly the same thing - a summary that
    disagreed with what the coach just said would be worse than no summary.
    """
    plans = session.exec(select(TrainingPlan).where(TrainingPlan.user_id == user_id)).all()

    result = []
    for plan in plans:
        sessions = session.exec(
            select(PlanSession)
            .where(PlanSession.plan_id == plan.id)
            .order_by(col(PlanSession.day_of_week))
        ).all()
        result.append(
            {
                "discipline": plan.discipline,
                "plan": plan.plan,
                # A proposal the user has not accepted yet. The coach needs to
                # know the difference: one is what they are doing, the other
                # is what was suggested.
                "status": "active" if plan.is_active else "awaiting_approval",
                "updated_at": plan.updated_at.isoformat(),
                "week": [
                    {
                        "day": DAY_KEYS[s.day_of_week],
                        "day_he": DAY_NAMES_HE[s.day_of_week],
                        "title": s.title,
                        "details": s.details,
                        "is_rest": s.is_rest,
                        "target_distance_km": s.target_distance_km,
                        "target_duration_minutes": s.target_duration_minutes,
                        "target_pace": s.target_pace,
                    }
                    for s in sessions
                ],
            }
        )
    return result


def fetch_personal_records(session: Session, user_id: int) -> list[dict]:
    """
    The stored records, shaped for the model.

    Both the raw value and the formatted string go out. The formatted one is
    what a reply should quote, so the model never has to turn 1310.3 seconds
    into 21:50 itself and get it wrong; the raw one with its unit and
    direction is what makes a comparison possible at all.
    """
    rows = session.exec(
        select(PersonalRecord).where(PersonalRecord.user_id == user_id)
    ).all()

    result = []
    for row in rows:
        meta = record_meta(row.record_type)
        if meta is None:
            continue
        _, sport, label_he, unit, lower_is_better = meta
        result.append(
            {
                "record_type": row.record_type,
                "sport": sport,
                "label_he": label_he,
                "value": row.value,
                "value_text": format_value(row.record_type, row.value),
                "unit": unit,
                "lower_is_better": lower_is_better,
                "achieved_at": row.achieved_at.isoformat() if row.achieved_at else None,
            }
        )
    return result


def fetch_calorie_status(session: Session, user: User, days: int = 7) -> dict:
    """
    Intake against measured burn, shared by the coach tool and the daily
    summary so the two can never disagree - the same reason
    fetch_training_plans is shared.

    A day with no burn figure carries burn: null and burn_source: "none"
    rather than a zero. Zero would read as "burned nothing", which is a
    claim, where null is the absence of one.
    """
    if user.daily_calorie_target is None:
        return {"tracking": False}

    days = max(1, min(days, 30))
    today = clock.today()
    entries = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        burned, source = burned_on(session, user.id, day)
        entry = {
            "date": day.isoformat(),
            "consumed": consumed_on(session, user.id, day),
            "burned": burned,
            "burn_source": source,
        }
        if is_burn_provisional(day, source):
            entry["burn_provisional"] = True
        entries.append(entry)

    return {
        "tracking": True,
        "daily_target": user.daily_calorie_target,
        "days": entries,
    }


def fetch_meal_plan(session: Session, user_id: int) -> Optional[dict]:
    plan = session.exec(select(MealPlan).where(MealPlan.user_id == user_id)).first()
    if plan is None:
        return None

    options = session.exec(
        select(MealOption).where(MealOption.plan_id == plan.id)
    ).all()

    meals: dict[str, list] = {}
    for option in options:
        meals.setdefault(option.meal_slot, []).append(
            {
                "title": option.title,
                "description": option.description,
                "calories": option.calories,
            }
        )

    return {
        "target_calories": plan.target_calories,
        "approach": plan.approach,
        "updated_at": plan.updated_at.isoformat(),
        "meals": meals,
    }


def _execute_tool(session: Session, user: User, name: str, tool_input: dict) -> str:
    """Runs one tool and returns its result as a JSON string for the API."""
    if name == "get_recent_daily_metrics":
        return json.dumps(fetch_daily_metrics(session, user.id, tool_input.get("days", 7)), ensure_ascii=False)

    if name == "get_recent_activities":
        return json.dumps(fetch_activities(session, user.id, tool_input.get("limit", 10)), ensure_ascii=False)

    if name == "get_training_goal":
        return json.dumps({"goal": user.training_goal}, ensure_ascii=False)

    if name == "set_training_goal":
        user.training_goal = tool_input.get("goal")
        session.add(user)
        session.commit()
        return json.dumps({"saved": True, "goal": user.training_goal}, ensure_ascii=False)

    if name == "get_training_plans":
        # updated_at travels with each plan: a plan written five weeks ago
        # should be revised against what the user has actually done since,
        # not recited as though it were still current.
        return json.dumps({"plans": fetch_training_plans(session, user.id)}, ensure_ascii=False)

    if name == "set_training_plan":
        discipline = tool_input.get("discipline")
        # A proposal, not the user's plan. It stays inactive until they press
        # approve, so revising it repeatedly during one conversation updates
        # the same draft and never disturbs the plan they are following.
        existing = session.exec(
            select(TrainingPlan).where(
                TrainingPlan.user_id == user.id,
                TrainingPlan.discipline == discipline,
                TrainingPlan.is_active == False,  # noqa: E712
            )
        ).first()
        if existing is None:
            existing = TrainingPlan(
                user_id=user.id,
                discipline=discipline,
                plan=tool_input.get("plan"),
                is_active=False,
            )
        else:
            existing.plan = tool_input.get("plan")
            existing.updated_at = datetime.now(timezone.utc)
        session.add(existing)
        session.commit()
        session.refresh(existing)

        sessions = tool_input.get("sessions")
        if sessions is not None:
            # Replace rather than merge. A revised week is a whole week - the
            # model sends the new schedule entire, and merging would leave
            # sessions from the old one stranded on days the new plan
            # deliberately left empty.
            session.exec(
                delete(PlanSession).where(
                    PlanSession.user_id == user.id,
                    PlanSession.plan_id == existing.id,
                )
            )
            for item in sessions:
                day = item.get("day")
                if day not in DAY_KEYS:
                    continue
                session.add(
                    PlanSession(
                        user_id=user.id,
                        plan_id=existing.id,
                        day_of_week=DAY_KEYS.index(day),
                        title=item.get("title") or "",
                        details=item.get("details"),
                        is_rest=bool(item.get("is_rest")),
                        target_distance_km=item.get("target_distance_km"),
                        target_duration_minutes=item.get("target_duration_minutes"),
                        target_pace=item.get("target_pace"),
                    )
                )
            session.commit()

        return json.dumps(
            {"saved": True, "discipline": discipline, "sessions": len(sessions or [])},
            ensure_ascii=False,
        )

    if name == "delete_training_plan":
        existing = session.exec(
            select(TrainingPlan).where(
                TrainingPlan.user_id == user.id,
                TrainingPlan.discipline == tool_input.get("discipline"),
            )
        ).first()
        if existing is not None:
            # Sessions first: they carry a foreign key to the plan, and there
            # is no ON DELETE CASCADE to fall back on.
            session.exec(delete(PlanSession).where(PlanSession.plan_id == existing.id))
            session.delete(existing)
            session.commit()
        return json.dumps({"deleted": existing is not None}, ensure_ascii=False)

    if name == "get_personal_records":
        return json.dumps(
            {"records": fetch_personal_records(session, user.id)}, ensure_ascii=False
        )

    if name == "get_calorie_status":
        return json.dumps(
            fetch_calorie_status(session, user, tool_input.get("days", 7)), ensure_ascii=False
        )

    if name == "get_meal_plan":
        plan = fetch_meal_plan(session, user.id)
        return json.dumps(plan or {"plan": None}, ensure_ascii=False)

    if name == "set_meal_plan":
        existing = session.exec(select(MealPlan).where(MealPlan.user_id == user.id)).first()
        if existing is None:
            existing = MealPlan(
                user_id=user.id,
                approach=tool_input.get("approach", ""),
                target_calories=tool_input.get("target_calories", 0),
            )
        else:
            existing.approach = tool_input.get("approach", existing.approach)
            existing.target_calories = tool_input.get("target_calories", existing.target_calories)
            existing.updated_at = datetime.now(timezone.utc)
        session.add(existing)
        session.commit()
        session.refresh(existing)

        # Replace rather than merge, the same rule set_training_plan follows:
        # a revised plan is a whole plan, and merging would strand options in
        # slots the new one deliberately emptied.
        session.exec(
            delete(MealOption).where(
                MealOption.user_id == user.id,
                MealOption.plan_id == existing.id,
            )
        )
        for item in tool_input.get("meals") or []:
            slot = item.get("slot")
            if slot not in MEAL_SLOTS:
                continue
            session.add(
                MealOption(
                    user_id=user.id,
                    plan_id=existing.id,
                    meal_slot=slot,
                    title=item.get("title") or "",
                    description=item.get("description"),
                    calories=item.get("calories"),
                )
            )
        session.commit()
        return json.dumps(
            {"saved": True, "meals": len(tool_input.get("meals") or [])}, ensure_ascii=False
        )

    if name == "delete_meal_plan":
        existing = session.exec(select(MealPlan).where(MealPlan.user_id == user.id)).first()
        if existing is not None:
            # Children first - they hold a foreign key and there is no
            # ON DELETE CASCADE behind it.
            session.exec(delete(MealOption).where(MealOption.plan_id == existing.id))
            session.delete(existing)
            session.commit()
        return json.dumps({"deleted": existing is not None}, ensure_ascii=False)

    if name == "get_athlete_profile":
        return json.dumps(profile_as_dict(session, user.id), ensure_ascii=False)

    if name == "get_health_alerts":
        return json.dumps(health_overview(session, user.id), ensure_ascii=False)

    if name == "update_athlete_profile":
        saved, rejected = update_profile(session, user.id, tool_input or {})
        return json.dumps({"saved": saved, "rejected": rejected}, ensure_ascii=False)

    if name == "remember":
        ok, reason = add_note(session, user.id, tool_input.get("note", ""))
        result = {"saved": ok, "reason": reason}
        if reason == "full":
            result["hint"] = "The note list is full. Tell the athlete they can remove old ones from /profile."
        return json.dumps(result, ensure_ascii=False)

    return json.dumps({"error": f"unknown tool {name}"})


# --------------------------------------------------------------------------
# Conversation
# --------------------------------------------------------------------------


def _load_history(session: Session, user_id: int) -> list[dict]:
    rows = session.exec(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id)
        .order_by(col(ChatMessage.created_at).desc())
        .limit(CHAT_HISTORY_LIMIT)
    ).all()
    # Query was newest-first (so the LIMIT keeps the *recent* ones); the API
    # needs oldest-first.
    history = [{"role": row.role, "content": row.content} for row in reversed(rows)]

    # The coach's unprompted messages (post-workout, alerts) are stored as
    # assistant turns, so the window can now begin with one - and the API
    # requires a conversation to open with the user. Drop the leading ones;
    # the most recent of them still sit later in the window.
    while history and history[0]["role"] == "assistant":
        history.pop(0)
    return history


def _store_message(session: Session, user_id: int, role: str, content: str) -> None:
    session.add(ChatMessage(user_id=user_id, role=role, content=content))
    session.commit()


def chat_with_coach(session: Session, user: User, user_message: str) -> tuple[str, set[str]]:
    """
    One conversational turn: replays recent history, lets Claude call data
    tools as needed, persists both sides, and returns the reply text plus the
    names of the tools that ran.

    The caller needs the tool names to know a training plan was proposed, so
    it can offer the approve button. Inferring that from the reply text would
    mean pattern-matching prose the model is free to word however it likes.
    """
    tools_used: set[str] = set()
    client = _client()

    history = _load_history(session, user.id)

    # Today's date is prepended to the *user turn*, never to the system
    # prompt: the system prompt is the cached prefix, and a date inside it
    # would change daily and silently destroy every cache hit.
    dated_message = f"[היום: {clock.today().isoformat()}]\n{user_message}"
    messages: list[dict] = history + [{"role": "user", "content": dated_message}]

    system = [
        {
            "type": "text",
            "text": COACH_SYSTEM_PROMPT,
            # The system prompt + tool definitions are identical on every
            # request, so caching them turns the largest fixed part of the
            # input into a ~90% cheaper cache read.
            "cache_control": {"type": "ephemeral"},
        }
    ]
    # The athlete's profile goes in a second block, *after* the cache point.
    # It differs per user and changes over time; inside the cached block it
    # would make every user's prefix unique and every edit a cache miss.
    profile_block = profile_prompt_block(session, user.id)
    if profile_block:
        system.append({"type": "text", "text": profile_block})

    response = None
    for _ in range(MAX_TOOL_ROUNDS):
        response = _call_claude(
            client,
            session=session,
            user_id=user.id,
            kind="chat",
            model=settings.claude_model,
            max_tokens=2000,
            system=system,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": "medium"},
        )

        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            # Recorded before the call, not after: the caller offers the
            # approve button because a plan was *proposed*, and a tool that
            # raised still changed nothing the user needs to approve.
            tools_used.add(block.name)
            try:
                result = _execute_tool(session, user, block.name, block.input)
            except Exception:
                logger.exception("Tool %s failed for user %s", block.name, user.id)
                result = json.dumps({"error": "tool failed"})
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
        messages.append({"role": "user", "content": tool_results})

    reply = "".join(block.text for block in response.content if block.type == "text").strip()
    if not reply:
        reply = "משהו השתבש בניסוח התשובה 🤔 תוכל לנסח את השאלה מחדש?"

    # Store the user's original text, not the date-prefixed version sent to
    # the API - the prefix is transport detail, and replaying old dates as
    # if current would confuse later turns.
    _store_message(session, user.id, "user", user_message)
    _store_message(session, user.id, "assistant", reply)

    return reply, tools_used


def generate_daily_summary(session: Session, user: User) -> Optional[str]:
    """
    Produces the end-of-day summary. Unlike chat, this pre-fetches the data
    instead of exposing tools: the query is fixed and known ahead of time,
    so a tool round-trip would just add a second API call, latency and cost
    for no added flexibility.

    Returns None when there's nothing to summarize, so the caller can skip
    sending rather than deliver an empty-handed message.
    """
    client = _client()

    metrics = fetch_daily_metrics(session, user.id, days=7)
    activities = fetch_activities(session, user.id, limit=5)
    if not metrics and not activities:
        return None

    today_date = clock.today()
    today = today_date.isoformat()

    tomorrow_index = week_index(today_date + timedelta(days=1))

    # Approved plans only: telling someone what they have tomorrow according
    # to a plan they never accepted would be inventing a commitment.
    plans = [p for p in fetch_training_plans(session, user.id) if p["status"] == "active"]
    tomorrow_sessions = [
        {"discipline": plan["discipline"], **item}
        for plan in plans
        for item in plan["week"]
        if DAY_KEYS.index(item["day"]) == tomorrow_index
    ]

    # Only added when the user opted in, so a non-tracking user's payload is
    # byte-identical to what it was before calories existed.
    calorie_block = fetch_calorie_status(session, user, days=3)
    meal_plan = fetch_meal_plan(session, user.id) if calorie_block.get("tracking") else None

    payload = {
        "today": today,
        "goal": user.training_goal,
        "athlete": profile_as_dict(session, user.id),
        "daily_metrics_last_7_days": metrics,
        # The athlete's own normal and anything outside it, so the summary
        # can say "still recovering" with the same numbers the alert used.
        "health": health_overview(session, user.id),
        "recent_activities": activities,
        # The whole plan, so adherence can be judged against what was
        # actually prescribed rather than a general sense of "enough".
        "training_plans": plans,
        # Pre-selected rather than left for the model to work out from the
        # day of week: getting tomorrow wrong is the one error that makes
        # this section worse than useless.
        "tomorrow": {
            "day": DAY_NAMES_HE[tomorrow_index],
            "sessions": tomorrow_sessions,
        },
    }

    if calorie_block.get("tracking"):
        payload["calories"] = calorie_block
        if meal_plan is not None:
            payload["meal_plan"] = meal_plan

    response = _call_claude(
        client,
        session=session,
        user_id=user.id,
        kind="daily_summary",
        model=settings.claude_model,
        max_tokens=600,
        system=[
            {
                "type": "text",
                "text": DAILY_SUMMARY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"הנה הנתונים של המשתמש. כתוב את סיכום היום עבור {today}.\n\n"
                    f"{json.dumps(payload, ensure_ascii=False)}"
                ),
            }
        ],
        # Low effort on purpose: this is a short, templated message following
        # explicit rules, not an analysis problem - paying for deep reasoning
        # here would be waste on a message that goes out daily per user.
        output_config={"effort": "low"},
    )

    return "".join(block.text for block in response.content if block.type == "text").strip() or None


def generate_weekly_summary(session: Session, user: User) -> Optional[str]:
    """
    The weekly message: a computed facts table plus a short read of it.

    The table is built in Python and never passes through the model - those
    numbers are already exact, and paying tokens to have them retyped would
    only introduce a chance of them coming back wrong. The model is given the
    same facts as structured data and asked for the one thing a template
    cannot produce: which of eight signals actually mattered this week, and
    what they mean together.

    Returns None when the week is empty, so the caller can stay quiet rather
    than send a summary of nothing.
    """
    facts = build_weekly_facts(session, user.id)

    week = facts["workouts"]["this_week"]
    if not week["count"] and facts["steps"]["this_week"] in (None, 0):
        return None

    table = format_weekly_facts(facts)

    client = _client()
    response = _call_claude(
        client,
        session=session,
        user_id=user.id,
        kind="weekly_summary",
        model=settings.claude_model,
        max_tokens=500,
        system=[
            {
                "type": "text",
                "text": WEEKLY_SUMMARY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    "הנה נתוני השבוע. כתוב את פסקת הסיכום.\n"
                    f"המטרה של המשתמש: {user.training_goal or 'לא הוגדרה'}\n\n"
                    f"{json.dumps(facts, ensure_ascii=False)}"
                ),
            }
        ],
        # Medium, unlike the daily summary's low: connecting several weekly
        # signals into one reading is the actual work here, where the daily
        # message follows a template.
        output_config={"effort": "medium"},
    )

    paragraph = "".join(b.text for b in response.content if b.type == "text").strip()
    return f"{table}\n\n{paragraph}" if paragraph else table


def generate_post_workout_note(session: Session, user: User, facts: dict) -> Optional[str]:
    """
    The short coaching paragraph under a finished workout. The numbers are
    shown above it by Python (format_activity_detail), so the model only
    adds what they do not say on their own - and is told not to repeat them.

    Low effort and a small ceiling: one message per workout per user is the
    most frequent AI call the coach makes on its own initiative.
    """
    client = _client()
    response = _call_claude(
        client,
        session=session,
        user_id=user.id,
        kind="post_workout",
        model=settings.claude_model,
        max_tokens=400,
        system=[
            {
                "type": "text",
                "text": POST_WORKOUT_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": "הנה נתוני האימון. כתוב את ההודעה.\n\n" + json.dumps(facts, ensure_ascii=False),
            }
        ],
        output_config={"effort": "low"},
    )
    return "".join(b.text for b in response.content if b.type == "text").strip() or None


def generate_anomaly_note(session: Session, user: User, facts: dict) -> Optional[str]:
    """
    The personal paragraph in an anomaly alert: what it might mean, and what
    to do with the next few planned sessions. The numbers are shown above it
    by Python, and the "not medical advice" line below it - the model writes
    neither.
    """
    client = _client()
    response = _call_claude(
        client,
        session=session,
        user_id=user.id,
        kind="anomaly",
        model=settings.claude_model,
        max_tokens=500,
        system=[
            {
                "type": "text",
                "text": ANOMALY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": "הנה מה שחרג. כתוב את ההודעה.\n\n" + json.dumps(facts, ensure_ascii=False, default=str),
            }
        ],
        output_config={"effort": "low"},
    )
    return "".join(b.text for b in response.content if b.type == "text").strip() or None
