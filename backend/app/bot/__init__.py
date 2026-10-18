"""
The Telegram bot - runs as its own process (docker-compose `bot`
service), separate from FastAPI and Celery. Uses long polling (the bot
actively asks Telegram "anything new?" in a loop) rather than webhooks,
since webhooks need a public HTTPS URL Telegram can reach and we don't
have one yet - switch to webhooks once there's a real deployment.

Interaction model: a persistent reply keyboard is the main menu, so the
common actions are one tap and nothing has to be memorized. Free-form
questions go to the AI coach only after the user taps 💬 and enters chat
mode; text typed outside it is pointed back at the buttons, so a stray
message never spends tokens.

Two things are intentionally kept cheap: the metrics/activities buttons
read straight from Postgres and never call Claude (no tokens spent
restating numbers we already have exactly), and Garmin credentials are
handled exactly once, at link time.

Layout - one module per area, so a change to calories cannot break
onboarding by accident:

  common.py           menu buttons, keyboards, the account gate, reply helpers
  onboarding.py       /start, terms, join requests, linking Garmin
  admin.py            owner-only /admin
  metrics.py          metrics buttons and quick lookups (no AI)
  activities.py       workouts by type, details, personal records
  manual_activity.py  adding a workout by hand
  calories.py         calorie tracking and reminders
  plans.py            training plans
  preferences.py      settings and account actions
  coach.py            chat with the AI coach
  application.py      registers every handler - the order there matters
"""

import logging

logging.basicConfig(level=logging.INFO)

# httpx logs every request at INFO with the full URL - and Telegram puts the
# bot token *inside* the URL path (api.telegram.org/bot<TOKEN>/getMe). At the
# polling rate that writes the token to disk continuously, so anyone who can
# read the container logs owns the bot. WARNING keeps genuine transport
# failures visible while dropping the per-request lines that carry the token.
logging.getLogger("httpx").setLevel(logging.WARNING)

from app.bot.application import build_application, main  # noqa: E402

__all__ = ["build_application", "main"]
