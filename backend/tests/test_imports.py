"""
Every process entry point must at least import.

Cheap, and exactly what catches the most common refactoring break: a moved
function, a renamed module, a circular import. Splitting bot.py into a
package is the next step, and this is the test that says it still loads.
"""

import importlib

import pytest


@pytest.mark.parametrize("module", ["app.bot", "app.tasks", "app.celery_app", "app.main"])
def test_entry_point_imports(module):
    importlib.import_module(module)


def test_bot_application_builds():
    # Registers every handler without contacting Telegram - a handler pointing
    # at a function that no longer exists fails here, not in production.
    from app.bot import build_application

    assert build_application() is not None


def test_coach_shares_one_client_with_a_timeout(monkeypatch):
    from app.core import claude_client
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(claude_client, "_shared_client", None)

    first, second = claude_client._client(), claude_client._client()

    assert first is second  # one connection pool, not a new handshake per call
    assert first.timeout == claude_client.CLAUDE_TIMEOUT_SECONDS
