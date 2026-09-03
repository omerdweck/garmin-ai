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
from datetime import date as date_type
from datetime import timedelta
from typing import Optional

import anthropic
from sqlmodel import Session, col, select

from app.core.claude_prompts import COACH_SYSTEM_PROMPT, DAILY_SUMMARY_SYSTEM_PROMPT
from app.core.config import settings
from app.models.activity import Activity
from app.models.chat_message import ChatMessage
from app.models.daily_metric import DailyMetric
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


def _client() -> anthropic.Anthropic:
    if not settings.is_claude_configured:
        raise ClaudeNotConfiguredError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _call_claude(client: anthropic.Anthropic, **kwargs):
    """
    Single place where the API is actually called, so every failure mode
    gets classified once instead of surfacing to the user as one generic
    "something went wrong".

    The distinction matters because the fixes are completely different:
    out of credit is something the *owner* has to top up, a bad key is a
    deployment problem, and a network blip just needs retrying. Telling
    the user which one it is saves them guessing.
    """
    try:
        return client.messages.create(**kwargs)
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
            "about how the user is feeling, recovering, sleeping, or trending."
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
]


def _metric_to_dict(metric: DailyMetric) -> dict:
    """Compact projection - only fields worth spending context tokens on."""
    fields = {
        "date": metric.date.isoformat(),
        "sleep_minutes": metric.sleep_duration_minutes,
        "deep_sleep_minutes": metric.deep_sleep_minutes,
        "rem_sleep_minutes": metric.rem_sleep_minutes,
        "resting_heart_rate": metric.resting_heart_rate,
        "max_heart_rate": metric.max_heart_rate,
        "hrv": metric.avg_hrv,
        "hrv_status": metric.hrv_status,
        "vo2_max": metric.vo2_max,
        "steps": metric.steps,
        "active_calories": metric.active_calories,
        "distance_meters": metric.distance_meters,
        "avg_stress": metric.avg_stress_level,
        "body_battery_high": metric.body_battery_high,
        "body_battery_low": metric.body_battery_low,
        "intensity_minutes": (metric.moderate_intensity_minutes or 0) + (metric.vigorous_intensity_minutes or 0),
    }
    # Dropping nulls keeps tool results small and stops the model from
    # reading "sleep_minutes: null" as "slept 0 hours".
    return {key: value for key, value in fields.items() if value is not None}


def _activity_to_dict(activity: Activity) -> dict:
    result = {
        "type": activity.activity_type,
        "name": activity.activity_name,
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
    cutoff = date_type.today() - timedelta(days=days)
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
    return [{"role": row.role, "content": row.content} for row in reversed(rows)]


def _store_message(session: Session, user_id: int, role: str, content: str) -> None:
    session.add(ChatMessage(user_id=user_id, role=role, content=content))
    session.commit()


def chat_with_coach(session: Session, user: User, user_message: str) -> str:
    """
    One conversational turn: replays recent history, lets Claude call data
    tools as needed, persists both sides, and returns the reply text.
    """
    client = _client()

    history = _load_history(session, user.id)

    # Today's date is prepended to the *user turn*, never to the system
    # prompt: the system prompt is the cached prefix, and a date inside it
    # would change daily and silently destroy every cache hit.
    dated_message = f"[היום: {date_type.today().isoformat()}]\n{user_message}"
    messages: list[dict] = history + [{"role": "user", "content": dated_message}]

    response = None
    for _ in range(MAX_TOOL_ROUNDS):
        response = _call_claude(
            client,
            model=settings.claude_model,
            max_tokens=2000,
            system=[
                {
                    "type": "text",
                    "text": COACH_SYSTEM_PROMPT,
                    # The system prompt + tool definitions are identical on
                    # every request, so caching them turns the largest fixed
                    # part of the input into a ~90% cheaper cache read.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
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

    return reply


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

    today = date_type.today().isoformat()
    payload = {
        "today": today,
        "goal": user.training_goal,
        "daily_metrics_last_7_days": metrics,
        "recent_activities": activities,
    }

    response = _call_claude(
        client,
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
