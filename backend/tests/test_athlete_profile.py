"""
The coach's long-term memory: validated on the way in, scoped to its owner,
readable and deletable by the athlete, and kept out of the cached prompt.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlmodel import select

from app.bot.profile import _render
from app.core import claude_client
from app.core.claude_prompts import COACH_SYSTEM_PROMPT
from app.core.config import settings
from app.models.athlete_profile import MAX_NOTES, CoachNote
from app.services.athlete_profile import (
    add_note,
    delete_note,
    get_profile,
    profile_as_dict,
    profile_prompt_block,
    update_profile,
)


def test_valid_fields_are_saved_and_invalid_ones_reported(session, make_user):
    me = make_user()
    saved, rejected = update_profile(session, me.id, {
        "sports": ["running", "swimming", "quidditch"],
        "training_days": ["tuesday", "funday", "saturday"],
        "pool_length_m": -25,
        "max_session_minutes": 60,
    })

    assert saved == {"sports": ["running", "swimming"], "training_days": ["tuesday", "saturday"], "max_session_minutes": 60}
    assert rejected == ["pool_length_m"]
    assert get_profile(session, me.id).pool_length_m is None


def test_notes_are_deduplicated_and_capped(session, make_user):
    me = make_user()
    assert add_note(session, me.id, "מעדיף לרוץ בבוקר") == (True, "saved")
    assert add_note(session, me.id, "מעדיף לרוץ בבוקר") == (False, "duplicate")
    assert add_note(session, me.id, "x" * 301) == (False, "too_long")

    for i in range(MAX_NOTES - 1):
        add_note(session, me.id, f"note {i}")
    assert add_note(session, me.id, "one too many") == (False, "full")


def test_a_note_can_only_be_deleted_by_its_owner(session, make_user):
    me, other = make_user(), make_user()
    add_note(session, other.id, "של מישהו אחר")
    their_note = session.exec(select(CoachNote).where(CoachNote.user_id == other.id)).one()

    # The id comes from a button, so a forged one must do nothing.
    assert delete_note(session, me.id, their_note.id) is False
    assert session.get(CoachNote, their_note.id) is not None
    assert delete_note(session, other.id, their_note.id) is True


def test_remember_tool_saves_a_note_for_the_caller(session, make_user):
    someone_else = make_user()  # created first, so the caller is not user 1
    me = make_user()
    claude_client._execute_tool(session, me, "remember", {"note": "יש לו מרתון במרץ"})

    assert [n["text"] for n in profile_as_dict(session, me.id)["notes"]] == ["יש לו מרתון במרץ"]
    assert profile_as_dict(session, someone_else.id)["notes"] == []


def test_profile_screen_has_a_delete_button_per_note(session, make_user):
    me = make_user()
    add_note(session, me.id, "א")
    add_note(session, me.id, "ב")
    ids = [n["id"] for n in profile_as_dict(session, me.id)["notes"]]

    _, keyboard = _render(profile_as_dict(session, me.id))
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]

    assert [f"profile:delnote:{i}" for i in ids] == callbacks[:2]
    assert "profile:clear" in callbacks


# --- the prompt ----------------------------------------------------------

def _fake_reply(text="בסדר"):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


@pytest.fixture
def captured(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    calls = []

    def fake_call(client, **kwargs):
        calls.append(kwargs)
        return _fake_reply()

    with patch.object(claude_client, "_call_claude", side_effect=fake_call):
        yield calls


def test_profile_goes_after_the_cache_point(session, make_user, captured):
    me = make_user()
    update_profile(session, me.id, {"training_days": ["tuesday"]})

    claude_client.chat_with_coach(session, me, "מה מחר?")

    cached, profile = captured[0]["system"]
    # The cached block is identical for every user, so the cache is shared;
    # the profile, which differs per user, sits after it, uncached.
    assert cached["text"] == COACH_SYSTEM_PROMPT and "cache_control" in cached
    assert "tuesday" in profile["text"] and "cache_control" not in profile


def test_no_profile_means_no_extra_block(session, make_user, captured):
    me = make_user()
    claude_client.chat_with_coach(session, me, "היי")
    assert len(captured[0]["system"]) == 1


def test_empty_profile_has_no_prompt_block(session, make_user):
    assert profile_prompt_block(session, make_user().id) is None
