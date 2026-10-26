"""
Which model writes what, and what it costs.

Short templated messages go to the light model (Haiku 5.5), everything that
needs judgement to the main one (Sonnet 5.5). A model missing from the price
table would silently switch off the monthly AI budget, so every model the
settings can name must have a price.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core import claude_client as cc
from app.core.claude_pricing import PRICES, calculate_cost
from app.core.config import settings


@pytest.mark.parametrize("kind", ["daily_summary", "post_workout", "anomaly"])
def test_short_messages_use_the_light_model(kind):
    assert cc.model_for(kind) == settings.claude_model_light


@pytest.mark.parametrize("kind", ["chat", "weekly_summary"])
def test_judgement_calls_use_the_main_model(kind):
    assert cc.model_for(kind) == settings.claude_model


def test_defaults_are_the_5_5_models():
    assert settings.model_fields["claude_model"].default == "claude-sonnet-5-5"
    assert settings.model_fields["claude_model_light"].default == "claude-haiku-5-5"


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-sonnet-5"])
def test_every_configurable_model_has_a_price(model):
    assert model in PRICES
    assert calculate_cost(model, 1000, 100) is not None


def test_haiku_costs_a_twentieth_of_sonnet_for_a_normal_prompt():
    haiku = calculate_cost("claude-haiku-5-5", 4000, 300, cache_read_tokens=3000)
    sonnet = calculate_cost("claude-sonnet-5-5", 4000, 300, cache_read_tokens=3000)
    assert haiku == pytest.approx(sonnet / 20)


def test_haiku_long_prompt_is_billed_at_the_higher_rate_on_every_line():
    # 100k counts fresh input and cache together; output is billed higher too.
    short = calculate_cost("claude-haiku-5-5", 60_000, 1_000, cache_read_tokens=40_000)
    long = calculate_cost("claude-haiku-5-5", 60_000, 1_000, cache_read_tokens=40_001)
    assert short == pytest.approx((60_000 * 0.10 + 1_000 * 0.50 + 40_000 * 0.01) / 1e6)
    assert long == pytest.approx((60_000 * 0.50 + 1_000 * 2.50 + 40_001 * 0.05) / 1e6)


def test_sonnet_has_no_long_prompt_tier():
    assert calculate_cost("claude-sonnet-5-5", 500_000, 0) == pytest.approx(500_000 * 2.00 / 1e6)


def _response(stop_reason, text=""):
    blocks = [SimpleNamespace(type="text", text=text)] if text else []
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(stop_reason=stop_reason, content=blocks, usage=usage)


def test_post_workout_note_is_written_by_the_light_model_with_room_to_think(session, make_user, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    user = make_user()
    sent = {}

    def fake_call(client, **kwargs):
        sent.update(kwargs)
        return _response("end_turn", "כל הכבוד!")

    with patch.object(cc, "_call_claude", fake_call), patch.object(cc, "_client", lambda: None):
        note = cc.generate_post_workout_note(session, user, {"activity": {"type": "running"}})

    assert note == "כל הכבוד!"
    assert sent["model"] == settings.claude_model_light
    assert sent["max_tokens"] == cc.LIGHT_MAX_TOKENS
