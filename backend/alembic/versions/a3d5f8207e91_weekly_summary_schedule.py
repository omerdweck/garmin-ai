"""weekly summary day and hour

Revision ID: a3d5f8207e91
Revises: f7c3e91d5b48
Create Date: 2026-09-07 17:30:00.000000

Both nullable, both NULL by default: nobody receives a weekly summary until
they choose when, which is the same opt-in shape the daily summary uses.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3d5f8207e91'
down_revision: Union[str, None] = 'f7c3e91d5b48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user', sa.Column('weekly_summary_day', sa.Integer(), nullable=True))
    op.add_column('user', sa.Column('weekly_summary_hour', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('user', 'weekly_summary_hour')
    op.drop_column('user', 'weekly_summary_day')
