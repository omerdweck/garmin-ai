"""activity detail

The processed second-by-second data of a workout, for the Mini App's
workout page. Nullable: filled once per workout, on demand.

Revision ID: c4f8a1e2d6b9
Revises: b7e4d2c9a1f3
Create Date: 2026-10-26 14:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4f8a1e2d6b9"
down_revision: Union[str, None] = "b7e4d2c9a1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("activity", sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("activity", sa.Column("detail_fetched_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("activity", "detail_fetched_at")
    op.drop_column("activity", "detail")
