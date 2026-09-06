"""per-user daily message limit

Revision ID: d3a90c15e774
Revises: c7e2f81b4d10
Create Date: 2026-09-06 09:40:00.000000

Nullable with no default on purpose. NULL means "use the global default from
settings", which is different from 0 meaning "no coach access". Backfilling
existing users with the current default would have frozen today's number into
every row, so a later change to the global limit would silently not apply to
anyone who registered before it.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3a90c15e774'
down_revision: Union[str, None] = 'c7e2f81b4d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user', sa.Column('daily_message_limit', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('user', 'daily_message_limit')
