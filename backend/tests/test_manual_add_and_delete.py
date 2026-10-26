"""
"➕ הוספת אימון": the past / future choice, and deleting a manual workout.

The bug this covers: a workout planned for later in the day went through
manual entry and was stored as done - in the history and the totals -
instead of becoming something to send to the watch.
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from app.bot import manual_activity as bot
from app.models.activity import SOURCE_GARMIN, SOURCE_MANUAL, Activity


def _press(user, data, user_data=None):
    chat = SimpleNamespace(id=user.telegram_chat_id, send_message=AsyncMock())
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=user.telegram_chat_id, chat=chat, reply_text=AsyncMock()),
    )
    update = SimpleNamespace(callback_query=query, effective_chat=chat)
    context = SimpleNamespace(user_data=user_data if user_data is not None else {})
    asyncio.run(bot.on_manual_step(update, context))
    return query, chat, context


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


# --- the first question ---------------------------------------------------------


def test_add_asks_past_or_future_first(make_user):
    user = make_user()
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=user.telegram_chat_id))
    with patch.object(bot, "_require_active", AsyncMock(return_value=user.id)):
        asyncio.run(bot.add_activity_start(update, SimpleNamespace(user_data={})))
    markup = message.reply_text.call_args.kwargs["reply_markup"]
    assert [b.callback_data for b in _buttons(markup)] == ["manual:past", "manual:future", "manual:abort"]


def test_past_opens_manual_entry(make_user):
    user = make_user()
    query, chat, context = _press(user, "manual:past")
    assert "רק לאימון שהשעון לא הקליט" in query.edit_message_text.call_args.args[0]
    types = _buttons(chat.send_message.call_args.kwargs["reply_markup"])
    assert types[0].callback_data.startswith("manual:type:")
    assert context.user_data[bot.MANUAL_KEY] == {}


def test_future_hands_over_to_the_builder_for_a_day(make_user, monkeypatch):
    monkeypatch.setattr(bot.settings, "anthropic_api_key", "sk-test")
    user = make_user()
    query, _, context = _press(user, "manual:future", {bot.MANUAL_KEY: {"type": "running"}})
    days = [b for b in _buttons(query.edit_message_text.call_args.kwargs["reply_markup"]) if b.callback_data.startswith("wkb:home:")]
    assert len(days) == bot.FUTURE_DAYS
    assert days[0].text == "היום" and days[1].text == "מחר"
    assert bot.MANUAL_KEY not in context.user_data  # nothing half-entered is left behind


def test_future_without_the_coach_says_so(make_user, monkeypatch):
    monkeypatch.setattr(bot.settings, "anthropic_api_key", None)
    query, _, _ = _press(make_user(), "manual:future")
    assert "לא מחובר" in query.edit_message_text.call_args.args[0]


def test_saving_offers_delete(session, make_user):
    user = make_user()
    query, _, _ = _press(user, "manual:save", {bot.MANUAL_KEY: {"type": "running", "day_offset": 0, "duration": 30}})
    saved = session.exec(select(Activity).where(Activity.user_id == user.id)).one()
    button = _buttons(query.edit_message_text.call_args.kwargs["reply_markup"])[0]
    assert button.callback_data == f"manual:del:{saved.id}"


# --- deleting -------------------------------------------------------------------


def manual(session, user, **fields):
    row = Activity(user_id=user.id, source=SOURCE_MANUAL, activity_type="running",
                   start_time=datetime.now() - timedelta(hours=1), duration_seconds=1800, **fields)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _exists(session, activity_id):
    session.expire_all()
    return session.get(Activity, activity_id) is not None


def test_delete_asks_before_deleting(session, make_user):
    user = make_user()
    row = manual(session, user)
    query, _, _ = _press(user, f"manual:del:{row.id}")
    assert "למחוק" in query.edit_message_text.call_args.args[0]
    assert _exists(session, row.id)


def test_confirmed_delete_removes_it(session, make_user):
    user = make_user()
    row = manual(session, user)
    query, _, _ = _press(user, f"manual:delok:{row.id}")
    assert "נמחק" in query.edit_message_text.call_args.args[0]
    assert not _exists(session, row.id)


def test_keep_leaves_it(session, make_user):
    user = make_user()
    row = manual(session, user)
    _press(user, f"manual:keep:{row.id}")
    assert _exists(session, row.id)


@pytest.mark.parametrize("whose", ["someone_else", "garmin"])
def test_cannot_delete_what_is_not_yours_or_not_manual(session, make_user, whose):
    me, other = make_user(), make_user()
    if whose == "someone_else":
        row = manual(session, other)
    else:
        row = manual(session, me)
        row.source = SOURCE_GARMIN
        session.add(row)
        session.commit()
    query, _, _ = _press(me, f"manual:delok:{row.id}")
    assert "לא נמצא" in query.edit_message_text.call_args.args[0]
    assert _exists(session, row.id)


def test_malformed_delete_is_ignored(make_user):
    query, _, _ = _press(make_user(), "manual:delok:abc")
    # Falls through to the normal steps, which don't know "delok" - nothing happens.
    assert not query.edit_message_text.called
