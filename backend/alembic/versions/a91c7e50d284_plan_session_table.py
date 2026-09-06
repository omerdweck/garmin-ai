"""plan_session table

Revision ID: a91c7e50d284
Revises: f2b4a67c9e13
Create Date: 2026-09-06 13:40:00.000000

Existing training_plan rows keep working with no sessions attached - the
plan text is unchanged and a plan without a week simply has nothing to say
about "tomorrow" until the coach next revises it.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # needed for sqlmodel.sql.sqltypes.AutoString - autogenerate doesn't add this import by itself


# revision identifiers, used by Alembic.
revision: str = 'a91c7e50d284'
down_revision: Union[str, None] = 'f2b4a67c9e13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'plan_session',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('details', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('is_rest', sa.Boolean(), nullable=False),
        sa.Column('target_distance_km', sa.Float(), nullable=True),
        sa.Column('target_duration_minutes', sa.Integer(), nullable=True),
        sa.Column('target_pace', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(['plan_id'], ['training_plan.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_plan_session_user_id'), 'plan_session', ['user_id'], unique=False)
    op.create_index(op.f('ix_plan_session_plan_id'), 'plan_session', ['plan_id'], unique=False)
    op.create_index(op.f('ix_plan_session_day_of_week'), 'plan_session', ['day_of_week'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_plan_session_day_of_week'), table_name='plan_session')
    op.drop_index(op.f('ix_plan_session_plan_id'), table_name='plan_session')
    op.drop_index(op.f('ix_plan_session_user_id'), table_name='plan_session')
    op.drop_table('plan_session')
