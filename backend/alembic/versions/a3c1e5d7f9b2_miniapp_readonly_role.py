"""miniapp read-only database role

The Mini App's API is the only part of the system exposed to the internet,
so it connects as its own role that can read and nothing more:

  - SELECT only, never INSERT/UPDATE/DELETE - a bug or an injection in the
    API cannot change anyone's data.
  - Named tables, not "all tables": a table added later is invisible to
    the API until a migration grants it, so a new sensitive table can't
    leak by default.
  - Not at all: chat_message (coach conversations), join_request,
    calorie_burn_override, usage_event.
  - Only some columns of user and garmin_account: never hashed_password,
    email or verification tokens, never the encrypted Garmin session.

The role is created without a password, so it cannot log in yet. Set one
once on the server, matching MINIAPP_DB_PASSWORD in .env:
    ALTER ROLE miniapp_ro WITH LOGIN PASSWORD '...';
Keeping the password out of the migration keeps it out of git.

Revision ID: a3c1e5d7f9b2
Revises: c016c930ad2b
Create Date: 2026-10-25 16:30:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a3c1e5d7f9b2"
down_revision: Union[str, None] = "c016c930ad2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE = "miniapp_ro"

# Whole tables the dashboard reads.
READABLE_TABLES = [
    "achievement",
    "activity",
    "athlete_profile",
    "coach_event",
    "coach_note",
    "daily_metric",
    "meal_option",
    "meal_plan",
    "calorie_entry",
    "metric_alert",
    "metric_baseline",
    "personal_record",
    "plan_session",
    "training_plan",
    "workout",
]

# Tables that also hold secrets: only these columns.
READABLE_COLUMNS = {
    "user": ["id", "telegram_chat_id", "is_active", "created_at"],
    "garmin_account": ["id", "user_id", "linked_at", "disconnected_at", "last_sync_at", "watch_last_upload_at"],
}


def upgrade() -> None:
    # Roles belong to the whole Postgres server, not one database, so this
    # must not fail when the role is already there (a second database on
    # the same server, or the test suite re-running the chain).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN;
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    for table in READABLE_TABLES:
        op.execute(f'GRANT SELECT ON TABLE "{table}" TO {ROLE}')
    for table, columns in READABLE_COLUMNS.items():
        cols = ", ".join(f'"{c}"' for c in columns)
        op.execute(f'GRANT SELECT ({cols}) ON TABLE "{table}" TO {ROLE}')


def downgrade() -> None:
    for table, columns in READABLE_COLUMNS.items():
        cols = ", ".join(f'"{c}"' for c in columns)
        op.execute(f'REVOKE SELECT ({cols}) ON TABLE "{table}" FROM {ROLE}')
    for table in READABLE_TABLES:
        op.execute(f'REVOKE SELECT ON TABLE "{table}" FROM {ROLE}')
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    # The role itself is left in place: another database on the same server
    # may still use it, and an unused NOLOGIN role is harmless.
