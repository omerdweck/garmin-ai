"""watch upload tracking on garmin_account

Revision ID: b41d7c9e2a03
Revises: 922ec3cff608
Create Date: 2026-09-06 08:05:00.000000

Both columns are nullable with no backfill: nobody has an upload time
recorded yet, and NULL is the honest representation of "we have never asked
Garmin". format_watch_sync_line treats NULL as "say nothing" rather than
inventing a placeholder date.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b41d7c9e2a03'
down_revision: Union[str, None] = '922ec3cff608'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('garmin_account', sa.Column('watch_last_upload_at', sa.DateTime(), nullable=True))
    op.add_column('garmin_account', sa.Column('watch_stale_notified_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('garmin_account', 'watch_stale_notified_at')
    op.drop_column('garmin_account', 'watch_last_upload_at')
