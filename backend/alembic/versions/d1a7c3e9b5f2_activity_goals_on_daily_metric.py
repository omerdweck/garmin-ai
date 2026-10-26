"""activity goals on daily_metric

Garmin's daily step goal and weekly intensity-minutes goal, for the home
screen's activity rings. Nullable: NULL means Garmin didn't send a goal
that day, and the reader falls back to the latest one it knows.

The raw daily summary of every stored day is already in raw_json, so the
columns are filled from it here - the rings work for the past weeks right
away instead of only from the next sync on. Only JSON numbers are copied;
anything else stays NULL.

Revision ID: d1a7c3e9b5f2
Revises: c4f8a1e2d6b9
Create Date: 2026-10-26 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1a7c3e9b5f2"
down_revision: Union[str, None] = "c4f8a1e2d6b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BACKFILL = {
    "daily_step_goal": "dailyStepGoal",
    "intensity_minutes_goal": "intensityMinutesGoal",
}


def upgrade() -> None:
    op.add_column("daily_metric", sa.Column("daily_step_goal", sa.Integer(), nullable=True))
    op.add_column("daily_metric", sa.Column("intensity_minutes_goal", sa.Integer(), nullable=True))
    for column, key in _BACKFILL.items():
        op.execute(
            f"""
            UPDATE daily_metric
            SET {column} = round((raw_json->'summary'->>'{key}')::numeric)::integer
            WHERE jsonb_typeof(raw_json->'summary'->'{key}') = 'number'
            """
        )


def downgrade() -> None:
    op.drop_column("daily_metric", "intensity_minutes_goal")
    op.drop_column("daily_metric", "daily_step_goal")
