"""usage_event table

Revision ID: c7e2f81b4d10
Revises: b41d7c9e2a03
Create Date: 2026-09-06 09:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # needed for sqlmodel.sql.sqltypes.AutoString - autogenerate doesn't add this import by itself


# revision identifiers, used by Alembic.
revision: str = 'c7e2f81b4d10'
down_revision: Union[str, None] = 'b41d7c9e2a03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'usage_event',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
        sa.Column('cache_write_tokens', sa.Integer(), nullable=False),
        # Nullable: an unknown model records its tokens with no cost rather
        # than a guessed one. See app/core/claude_pricing.py.
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_usage_event_user_id'), 'usage_event', ['user_id'], unique=False)
    op.create_index(op.f('ix_usage_event_created_at'), 'usage_event', ['created_at'], unique=False)
    op.create_index(op.f('ix_usage_event_kind'), 'usage_event', ['kind'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_usage_event_kind'), table_name='usage_event')
    op.drop_index(op.f('ix_usage_event_created_at'), table_name='usage_event')
    op.drop_index(op.f('ix_usage_event_user_id'), table_name='usage_event')
    op.drop_table('usage_event')
