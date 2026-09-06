"""training load and readiness on daily_metric

Revision ID: e5c81f3d7b26
Revises: d3a90c15e774
Create Date: 2026-09-06 11:20:00.000000

All nullable, no backfill. These come from two extra Garmin calls per day
that are allowed to fail without failing a sync, so NULL is a normal value
meaning "Garmin did not give us this", not a gap to be filled. Existing rows
pick the values up whenever their day is next synced.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # needed for sqlmodel.sql.sqltypes.AutoString - autogenerate doesn't add this import by itself


# revision identifiers, used by Alembic.
revision: str = 'e5c81f3d7b26'
down_revision: Union[str, None] = 'd3a90c15e774'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('daily_metric', sa.Column('training_load_acute', sa.Integer(), nullable=True))
    op.add_column('daily_metric', sa.Column('training_load_chronic', sa.Integer(), nullable=True))
    op.add_column('daily_metric', sa.Column('training_status', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('daily_metric', sa.Column('load_balance', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('daily_metric', sa.Column('training_readiness_score', sa.Integer(), nullable=True))
    op.add_column('daily_metric', sa.Column('training_readiness_level', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('daily_metric', sa.Column('recovery_time_minutes', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('daily_metric', 'recovery_time_minutes')
    op.drop_column('daily_metric', 'training_readiness_level')
    op.drop_column('daily_metric', 'training_readiness_score')
    op.drop_column('daily_metric', 'load_balance')
    op.drop_column('daily_metric', 'training_status')
    op.drop_column('daily_metric', 'training_load_chronic')
    op.drop_column('daily_metric', 'training_load_acute')
