"""notifications

The table behind the Mini App's bell: a copy of every unprompted message
the bot sent (app/models/notification.py).

It is also the first table the Mini App's role may write to, and only
narrowly: UPDATE of the one column read_at. "Mark as read" has to be stored
somewhere the page can change, and a column-level grant keeps that from
becoming a way to rewrite what the bot said or add messages it never sent.
Postgres needs SELECT as well for the UPDATE's WHERE clause, and the screen
reads the rows anyway.

Revision ID: d5e2a9c47b18
Revises: d1a7c3e9b5f2
Create Date: 2026-10-26 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5e2a9c47b18"
down_revision: Union[str, None] = "d1a7c3e9b5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("link", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "source_key", name="uq_notification_user_source"),
    )
    op.create_index("ix_notification_user_id_id", "notification", ["user_id", "id"], unique=False)
    op.execute('GRANT SELECT ON TABLE "notification" TO miniapp_ro')
    op.execute('GRANT UPDATE ("read_at") ON TABLE "notification" TO miniapp_ro')


def downgrade() -> None:
    op.execute('REVOKE UPDATE ("read_at") ON TABLE "notification" FROM miniapp_ro')
    op.execute('REVOKE SELECT ON TABLE "notification" FROM miniapp_ro')
    op.drop_index("ix_notification_user_id_id", table_name="notification")
    op.drop_table("notification")
