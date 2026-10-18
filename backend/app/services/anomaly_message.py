"""
The anomaly alert message. Three parts, like the post-workout one:
  1. what stepped outside the athlete's normal - Python, exact numbers
  2. what it might mean, and what to do with the next planned sessions - Claude
  3. a fixed line that this is not medical advice - always, never left to the model

Without Claude, part 2 becomes a short fixed hint per rule, so the alert
still goes out.
"""

import logging
from datetime import timedelta
from typing import Optional

from sqlmodel import Session, col, select

from app.core import clock
from app.core.claude_client import (
    ClaudeAuthError,
    ClaudeNotConfiguredError,
    ClaudeOutOfCreditError,
    ClaudeUnavailableError,
    fetch_activities,
    fetch_training_plans,
    generate_anomaly_note,
)
from app.models.coach_event import KIND_ANOMALY, CoachEvent
from app.models.metric_alert import MetricAlert
from app.models.plan_session import DAY_KEYS, DAY_NAMES_HE, week_index
from app.models.user import User
from app.services import anomaly_rules as rules
from app.services.athlete_profile import profile_as_dict
from app.services.coach_events import register_composer

logger = logging.getLogger(__name__)

HEADER = "⚠️ *שמתי לב למשהו בימים האחרונים*"
FOOTER = "_זה לא ייעוץ רפואי. אם משהו מרגיש לא בסדר, כדאי להתייעץ עם רופא._"

# Used only when Claude is unavailable.
FALLBACK_HINTS = {
    rules.RULE_HRV: "הגוף אולי עוד מתאושש. כדאי לשקול יום קל או מנוחה, ולשים לב לשינה ולשתייה.",
    rules.RULE_RHR: "הגוף אולי עוד מתאושש. כדאי לשקול יום קל או מנוחה, ולשים לב לשינה ולשתייה.",
    rules.RULE_BODY_BATTERY: "הגוף אולי עוד מתאושש. כדאי לשקול יום קל או מנוחה, ולשים לב לשינה ולשתייה.",
    rules.RULE_SLEEP: "לילה או שניים של שינה מסודרת יעשו הבדל גדול. אם אפשר, להקל את האימון הבא.",
    rules.RULE_EASY_RUN: "זה יכול להיות עייפות מצטברת, חום או מעט שתייה. שווה להאט בריצה הקלה הבאה.",
    rules.RULE_LOAD: "עלייה חדה בעומס מעלה את הסיכון לפציעה. כדאי לא להוסיף עוד בשבוע הקרוב.",
}


def _hours(minutes: Optional[int]) -> str:
    if minutes is None:
        return "?"
    return f"{minutes // 60}:{minutes % 60:02d}"


def format_alert(rule: str, details: dict) -> str:
    d = details or {}
    if rule == rules.RULE_HRV:
        return f"📉 *HRV* ירד 3 ימים ברצף: {' ← '.join(str(v) for v in d['values'])} (הרגיל שלך: ~{d['baseline']})"
    if rule == rules.RULE_RHR:
        return f"❤️ *דופק מנוחה* גבוה: {', '.join(str(v) for v in d['values'])} (הרגיל שלך: ~{d['baseline']})"
    if rule == rules.RULE_SLEEP:
        nights = ", ".join(
            _hours(n["minutes"]) + (f" (ציון {n['score']})" if n.get("score") is not None else "")
            for n in d["nights"]
        )
        return f"😴 *שינה*: {d['poor']} מתוך הלילות האחרונים היו קצרים או חלשים. שעות: {nights}"
    if rule == rules.RULE_BODY_BATTERY:
        return (
            f"🔋 *Body Battery* לא נטען מעל {d['threshold']} כבר 3 ימים: "
            f"{', '.join(str(v) for v in d['values'])}"
        )
    if rule == rules.RULE_EASY_RUN:
        return f"🏃 בריצות הקלות האחרונות הלב עובד קשה בכ-{d['drop_pct']}% יותר מהרגיל שלך, ביחס לקצב"
    if rule == rules.RULE_LOAD:
        lines = []
        if "acwr" in d:
            lines.append(f"📈 *עומס אימונים*: יחס אקוטי:כרוני {d['acwr']} (גבוה)")
        if "jump_pct" in d:
            lines.append(
                f"📈 *נפח*: {d['last_week_minutes']} דק' בשבוע האחרון, לעומת ממוצע "
                f"{d['previous_weekly_minutes']} דק' בחודש שלפני (+{d['jump_pct']}%)"
            )
        return "\n".join(lines)
    return rule


def upcoming_sessions(session: Session, user_id: int, days: int = 3) -> list[dict]:
    """Approved-plan sessions for today and the next days, with real dates."""
    plans = [p for p in fetch_training_plans(session, user_id) if p["status"] == "active"]
    today = clock.today()
    result = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        key = DAY_KEYS[week_index(day)]
        for plan in plans:
            for item in plan["week"]:
                if item["day"] == key:
                    result.append(
                        {"date": day.isoformat(), "day_he": DAY_NAMES_HE[week_index(day)],
                         "discipline": plan["discipline"], "title": item["title"],
                         "details": item.get("details"), "is_rest": item.get("is_rest")}
                    )
    return result


@register_composer(KIND_ANOMALY, uses_ai=True)
def compose_anomaly(session: Session, user: User, event: CoachEvent) -> Optional[str]:
    ids = (event.payload or {}).get("alert_ids") or []
    alerts = session.exec(
        select(MetricAlert)
        .where(col(MetricAlert.id).in_(ids), MetricAlert.user_id == user.id)
        .order_by(col(MetricAlert.id))
    ).all()
    if not alerts:
        return None

    facts = {
        "alerts": [
            {"rule": a.rule, "details": a.details, "started_on": a.started_on.isoformat()} for a in alerts
        ],
        "upcoming_sessions": upcoming_sessions(session, user.id),
        "recent_activities": fetch_activities(session, user.id, limit=5),
        "athlete": profile_as_dict(session, user.id),
    }
    try:
        note = generate_anomaly_note(session, user, facts)
    except (ClaudeNotConfiguredError, ClaudeAuthError, ClaudeOutOfCreditError, ClaudeUnavailableError) as exc:
        logger.warning("Anomaly alert without AI for user %s - %s", user.id, type(exc).__name__)
        note = None
    if not note:
        note = " ".join(dict.fromkeys(FALLBACK_HINTS[a.rule] for a in alerts if a.rule in FALLBACK_HINTS))

    lines = "\n".join(format_alert(a.rule, a.details) for a in alerts)
    return "\n\n".join(part for part in (HEADER, lines, note, FOOTER) if part)
