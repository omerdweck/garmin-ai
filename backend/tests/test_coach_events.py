"""
Every unprompted coach message goes through record_event -> deliver.
These pin the rules that decide whether a user hears from the coach.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlmodel import select

import app.services.coach_events as ce
from app.core.claude_client import _load_history, _store_message
from app.models.chat_message import ChatMessage
from app.models.coach_event import KIND_ANOMALY, KIND_POST_WORKOUT, STATUS_PENDING, CoachEvent
from app.models.garmin_account import GarminAccount
from app.models.usage_event import UsageEvent


@pytest.fixture
def sent():
    with patch.object(ce, "send_telegram_message") as fake:
        yield fake


@pytest.fixture
def composers(monkeypatch):
    """Template composers for both kinds, so delivery can be tested alone."""
    monkeypatch.setitem(ce.COMPOSERS, KIND_POST_WORKOUT, ce.Composer(lambda s, u, e: "כל הכבוד!", uses_ai=True))
    monkeypatch.setitem(ce.COMPOSERS, KIND_ANOMALY, ce.Composer(lambda s, u, e: "שים לב ל-HRV", uses_ai=False))


def _record(session, user, kind=KIND_POST_WORKOUT, key="post_workout:1"):
    return ce.record_event(session, user.id, kind, key, {"activity_id": 1})


# --- recording -----------------------------------------------------------

def test_the_same_event_is_recorded_once(session, make_user):
    me = make_user()

    first = _record(session, me)
    second = _record(session, me)

    assert first is not None and second is None
    assert len(session.exec(select(CoachEvent)).all()) == 1


def test_the_same_key_for_two_users_is_two_events(session, make_user):
    me, other = make_user(), make_user()

    assert _record(session, me) is not None
    assert _record(session, other) is not None


# --- delivery ------------------------------------------------------------

def test_delivered_message_is_sent_with_the_talk_button_and_kept_in_history(session, make_user, sent, composers):
    me = make_user()
    event = _record(session, me)

    assert ce.deliver(session, event.id) == "sent"

    sent.assert_called_once()
    assert sent.call_args.kwargs["reply_markup"] is ce.TALK_KEYBOARD
    history = session.exec(select(ChatMessage).where(ChatMessage.user_id == me.id)).all()
    assert [(m.role, m.content) for m in history] == [("assistant", "כל הכבוד!")]


def test_an_event_is_never_sent_twice(session, make_user, sent, composers):
    me = make_user()
    event = _record(session, me)

    ce.deliver(session, event.id)
    assert ce.deliver(session, event.id) == "already_handled"

    sent.assert_called_once()


def test_turned_off_kind_is_not_sent(session, make_user, sent, composers):
    me = make_user(notify_post_workout=False)
    event = _record(session, me)

    assert ce.deliver(session, event.id) == "skipped:turned_off"
    sent.assert_not_called()


def test_disconnected_user_hears_nothing(session, make_user, sent, composers):
    me = make_user()
    account = session.exec(select(GarminAccount).where(GarminAccount.user_id == me.id)).one()
    account.disconnected_at = datetime.now(timezone.utc)
    session.add(account)
    session.commit()

    event = _record(session, me)

    assert ce.deliver(session, event.id) == "skipped:not_active"
    sent.assert_not_called()


def test_at_most_one_anomaly_alert_per_day(session, make_user, sent, composers):
    me = make_user()
    first = _record(session, me, KIND_ANOMALY, "anomaly:hrv:2026-10-18")
    second = _record(session, me, KIND_ANOMALY, "anomaly:rhr:2026-10-18")

    assert ce.deliver(session, first.id) == "sent"
    assert ce.deliver(session, second.id) == "skipped:daily_limit"
    assert sent.call_count == 1


def test_anomaly_limit_resets_the_next_israeli_day(session, make_user, sent, composers):
    me = make_user()
    yesterday = _record(session, me, KIND_ANOMALY, "anomaly:hrv:1")
    ce.deliver(session, yesterday.id)
    # Sent "yesterday": 25 hours ago is always another calendar day.
    yesterday.sent_at = datetime.now(timezone.utc) - timedelta(hours=25)
    session.add(yesterday)
    session.commit()

    today = _record(session, me, KIND_ANOMALY, "anomaly:hrv:2")
    assert ce.deliver(session, today.id) == "sent"


def test_post_workout_messages_have_no_daily_limit(session, make_user, sent, composers):
    # Two workouts in a day deserve two messages.
    me = make_user()
    for key in ("post_workout:1", "post_workout:2"):
        assert ce.deliver(session, _record(session, me, key=key).id) == "sent"


def test_ai_budget_stops_ai_messages_but_not_template_ones(session, make_user, sent, composers):
    me = make_user()
    session.add(UsageEvent(user_id=me.id, kind="daily_summary", model="m", cost_usd=10_000.0))
    session.commit()

    ai_event = _record(session, me, KIND_POST_WORKOUT, "post_workout:1")
    template_event = _record(session, me, KIND_ANOMALY, "anomaly:1")

    assert ce.deliver(session, ai_event.id) == "skipped:ai_budget"
    assert ce.deliver(session, template_event.id) == "sent"


def test_nothing_to_say_sends_nothing(session, make_user, sent, monkeypatch):
    monkeypatch.setitem(ce.COMPOSERS, KIND_POST_WORKOUT, ce.Composer(lambda s, u, e: None, uses_ai=False))
    me = make_user()

    assert ce.deliver(session, _record(session, me).id) == "skipped:nothing_to_say"
    sent.assert_not_called()


def test_failed_send_leaves_the_event_pending_for_the_retry(session, make_user, sent, composers):
    me = make_user()
    event = _record(session, me)
    sent.side_effect = RuntimeError("Telegram is down")

    with pytest.raises(RuntimeError):
        ce.deliver(session, event.id)

    session.rollback()
    assert session.get(CoachEvent, event.id).status == STATUS_PENDING


# --- the coach's history -------------------------------------------------

def test_history_never_starts_with_a_coach_message(session, make_user):
    # The API requires a conversation to open with the user; an unprompted
    # message can now be the oldest row in the window.
    me = make_user()
    _store_message(session, me.id, "assistant", "כל הכבוד על הריצה!")
    _store_message(session, me.id, "user", "תודה, איך היה הקצב?")
    _store_message(session, me.id, "assistant", "יציב.")

    history = _load_history(session, me.id)

    assert history[0]["role"] == "user"
    assert [h["content"] for h in history] == ["תודה, איך היה הקצב?", "יציב."]


def test_every_settings_toggle_maps_to_a_real_column_that_starts_on(make_user):
    from app.bot.preferences import NOTIFY_OPTIONS

    me = make_user()
    for column, _label in NOTIFY_OPTIONS.values():
        assert getattr(me, column) is True
    assert {c for c, _ in NOTIFY_OPTIONS.values()} == set(ce.PREFERENCE_FOR_KIND.values())


# --- the "📊 לניתוח המלא" button under a post-workout message -----------------


def test_post_workout_message_links_to_its_workout_page(monkeypatch):
    monkeypatch.setattr(ce.settings, "miniapp_url", "https://garmin-ai.duckdns.org")
    event = CoachEvent(user_id=1, kind=KIND_POST_WORKOUT, key="post_workout:9", payload={"activity_id": 42})
    rows = ce.keyboard_for(event).inline_keyboard
    assert rows[0][0].web_app.url == "https://garmin-ai.duckdns.org/?activity=42"
    assert rows[1][0].callback_data == ce.TALK_CALLBACK  # "💬 לדבר על זה" still there


@pytest.mark.parametrize(
    "url, kind, payload",
    [
        ("", KIND_POST_WORKOUT, {"activity_id": 42}),            # no Mini App configured
        ("https://x.example", KIND_ANOMALY, {"activity_id": 42}),  # not a workout message
        ("https://x.example", KIND_POST_WORKOUT, {}),             # no workout to open
    ],
)
def test_no_workout_button_when_it_cannot_work(monkeypatch, url, kind, payload):
    monkeypatch.setattr(ce.settings, "miniapp_url", url)
    event = CoachEvent(user_id=1, kind=kind, key="k", payload=payload)
    assert ce.keyboard_for(event) is ce.TALK_KEYBOARD
