"""
The migrations must build exactly the schema the models describe.

The other tests create tables straight from the models, so on their own
they would stay green while production - which is built by `alembic
upgrade head` in deploy.sh - drifts away: a column added to a model with no
migration works in every test and crashes on the server. This test runs
the real migration chain on an empty database and compares the result with
the models.
"""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlmodel import SQLModel

from app.db.session import engine

BACKEND = Path(__file__).resolve().parent.parent


def _reset_schema():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def test_migrations_match_the_models():
    _reset_schema()
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    try:
        command.upgrade(config, "head")

        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), SQLModel.metadata)

        assert diff == [], (
            "Models and migrations disagree - run `alembic revision --autogenerate` "
            f"and review the result. Differences: {diff}"
        )
    finally:
        _reset_schema()  # leave nothing behind for the next test
