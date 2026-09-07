"""race predictions on daily_metric

Revision ID: f7c3e91d5b48
Revises: e18b6f042c9a
Create Date: 2026-09-07 16:00:00.000000

Nullable with no backfill. The next sync fills the whole window in one
ranged call, so history appears on its own rather than needing a data
migration.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7c3e91d5b48'
down_revision: Union[str, None] = 'e18b6f042c9a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in (
        'race_predict_5k_seconds',
        'race_predict_10k_seconds',
        'race_predict_half_seconds',
        'race_predict_marathon_seconds',
    ):
        op.add_column('daily_metric', sa.Column(column, sa.Integer(), nullable=True))


def downgrade() -> None:
    for column in (
        'race_predict_marathon_seconds',
        'race_predict_half_seconds',
        'race_predict_10k_seconds',
        'race_predict_5k_seconds',
    ):
        op.drop_column('daily_metric', column)
