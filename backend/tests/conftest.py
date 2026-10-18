"""
Shared test setup.

The environment is filled in *before* anything from `app` is imported:
`app.core.config.settings` and the DB engine are built at import time, so
an env var set afterwards would simply be ignored.

Every test gets an empty schema and leaves nothing behind. The tables are
created from the SQLModel metadata (not by running Alembic) because the
tests check what the code does, not the migration history - and it keeps
each test independent of the order the others ran in.
"""

import os

# setdefault, not assignment: docker compose and CI pass their own values
# (host, port), and those must win. These defaults are for a bare `pytest`.
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "garmin_test")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")

# Dummy secrets. Real ones must never be needed to run the tests - a test
# that only passes with the production key is a test nobody else can run.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("FERNET_KEY", "-KsPbgqFFBtshsrKRvGker143w1spbSDASFGp0dRV0E=")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000:test-token")
os.environ["ANTHROPIC_API_KEY"] = ""  # no test may reach the real API

# No PGTZ here on purpose: app/db/session.py pins every connection to UTC
# itself, and test_clock.py checks that it does. Setting it here as well
# would hide a regression in exactly the thing being tested.
os.environ.pop("PGTZ", None)

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

import app.models  # noqa: E402,F401  - registers every table on the metadata
from app.core.config import settings  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.models.activity import Activity  # noqa: E402
from app.models.garmin_account import GarminAccount  # noqa: E402
from app.models.user import User  # noqa: E402

# The guard that matters most in this file. Every test ends with drop_all,
# so pointed at the real database the suite would erase everyone's data.
# Refusing anything not named *_test makes that mistake impossible rather
# than merely unlikely.
if not settings.postgres_db.endswith("_test"):
    raise RuntimeError(
        f"Refusing to run tests against database '{settings.postgres_db}'. "
        "The test database name must end with '_test'."
    )


@pytest.fixture
def session():
    SQLModel.metadata.drop_all(engine)  # leftovers from an interrupted run
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    SQLModel.metadata.drop_all(engine)


_next_chat_id = iter(range(100_000, 200_000))


@pytest.fixture
def make_user(session):
    """Factory: a fully onboarded user (terms accepted), optionally linked."""

    def _make(linked: bool = True, **fields) -> User:
        user = User(
            telegram_chat_id=next(_next_chat_id),
            is_active=True,
            terms_accepted_at=datetime.now(timezone.utc),
            **fields,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        if linked:
            session.add(GarminAccount(user_id=user.id, encrypted_token="not-a-real-token"))
            session.commit()
        return user

    return _make


@pytest.fixture
def make_activity(session):
    def _make(user: User, **fields) -> Activity:
        defaults = {
            "activity_name": "ריצה",
            "activity_type": "running",
            "start_time": datetime.now(timezone.utc),
            "duration_seconds": 1800.0,
            "distance_meters": 5000.0,
        }
        activity = Activity(user_id=user.id, **{**defaults, **fields})
        session.add(activity)
        session.commit()
        session.refresh(activity)
        return activity

    return _make
