"""
Handler order is behaviour in python-telegram-bot: within a group the first
matching handler wins. These pin the orderings the bot depends on, so moving
code between modules (or adding a handler in the wrong place) fails here
instead of silently sending a button tap to the AI coach.
"""

import pytest
from telegram.ext import CallbackQueryHandler, MessageHandler

from app.bot import build_application
from app.bot.coach import talk_to_coach
from app.bot.common import MENU_BUTTONS
from app.bot.manual_activity import on_manual_text


@pytest.fixture(scope="module")
def handlers():
    return build_application().handlers


def _callbacks(group):
    return [getattr(h, "callback", None) for h in group]


def _pattern_position(group, pattern):
    for index, h in enumerate(group):
        if isinstance(h, CallbackQueryHandler) and getattr(h.pattern, "pattern", None) == pattern:
            return index
    raise AssertionError(f"no handler for {pattern}")


def test_typed_text_reaches_the_form_before_the_coach(handlers):
    # Group -1 runs first: a "37" typed into the calorie form must never be
    # sent to Claude as a question.
    assert _callbacks(handlers[-1]) == [on_manual_text]


def test_the_coach_is_the_last_resort_for_text(handlers):
    main = handlers[0]
    text_handlers = [h for h in main if isinstance(h, MessageHandler)]
    assert text_handlers[-1].callback is talk_to_coach


def test_every_menu_button_has_a_handler_before_the_coach(handlers):
    main = handlers[0]
    coach_at = _callbacks(main).index(talk_to_coach)
    button_handlers = [h for h in main[:coach_at] if isinstance(h, MessageHandler)]
    covered = {
        text
        for h in button_handlers
        for text in (getattr(h.filters, "strings", None) or ())
    }
    assert set(MENU_BUTTONS) <= covered


@pytest.mark.parametrize(
    "narrow, broad",
    [
        (r"^plancancel:", r"^plan:"),
        (r"^calrem:", r"^cal:"),
        (r"^acttype_menu$", r"^acttype:"),
    ],
)
def test_narrow_callback_patterns_come_first(handlers, narrow, broad):
    main = handlers[0]
    assert _pattern_position(main, narrow) < _pattern_position(main, broad)


def test_new_buttons_are_wired(handlers):
    main = handlers[0]
    for pattern in (r"^notify:", r"^talk:", r"^profile:"):
        _pattern_position(main, pattern)  # raises if missing
