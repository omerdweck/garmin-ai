"""history import progress

One row per user tracking the one-time import of their past year from
Garmin (see app/services/history_import.py). Readable by the Mini App's
read-only role, which shows the progress bar from it.

Revision ID: b7e4d2c9a1f3
Revises: a3c1e5d7f9b2
Create Date: 2026-10-25 17:40:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "b7e4d2c9a1f3"
down_revision: Union[str, None] = "a3c1e5d7f9b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "history_import",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("newest_date", sa.Date(), nullable=False),
        sa.Column("oldest_date", sa.Date(), nullable=False),
        sa.Column("next_date", sa.Date(), nullable=False),
        sa.Column("activities_done", sa.Boolean(), nullable=False),
        sa.Column("activities_imported", sa.Integer(), nullable=False),
        sa.Column("days_total", sa.Integer(), nullable=False),
        sa.Column("days_done", sa.Integer(), nullable=False),
        sa.Column("days_fetched", sa.Integer(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_history_import_status"), "history_import", ["status"], unique=False)
    op.create_index(op.f("ix_history_import_user_id"), "history_import", ["user_id"], unique=True)
    op.execute('GRANT SELECT ON TABLE "history_import" TO miniapp_ro')


def downgrade() -> None:
    op.execute('REVOKE SELECT ON TABLE "history_import" FROM miniapp_ro')
    op.drop_index(op.f("ix_history_import_user_id"), table_name="history_import")
    op.drop_index(op.f("ix_history_import_status"), table_name="history_import")
    op.drop_table("history_import")
