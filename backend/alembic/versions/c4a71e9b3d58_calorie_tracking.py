"""calorie tracking: target, reminders, entries and burn overrides

Revision ID: c4a71e9b3d58
Revises: b8d3f24a71c5
Create Date: 2026-09-07 12:00:00.000000

Every column is nullable and every table starts empty, so existing users are
untouched: daily_calorie_target stays NULL, which is exactly the "tracking is
off" state the feature is built around.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a71e9b3d58'
down_revision: Union[str, None] = 'b8d3f24a71c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL here is what switches the whole feature off for everyone who has
    # not opted in - there is no separate enabled flag to keep in step.
    op.add_column('user', sa.Column('daily_calorie_target', sa.Integer(), nullable=True))
    op.add_column('user', sa.Column('calorie_reminder_hour_1', sa.Integer(), nullable=True))
    op.add_column('user', sa.Column('calorie_reminder_hour_2', sa.Integer(), nullable=True))
    op.add_column('user', sa.Column('calorie_reminder_hour_3', sa.Integer(), nullable=True))
    op.add_column('user', sa.Column('calorie_abandon_prompted_at', sa.DateTime(), nullable=True))

    # Many rows per day, summed.
    op.create_table(
        'calorie_entry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('calories', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_calorie_entry_user_id'), 'calorie_entry', ['user_id'], unique=False)
    op.create_index(op.f('ix_calorie_entry_entry_date'), 'calorie_entry', ['entry_date'], unique=False)

    # One row per day, replacing rather than adding - hence the constraint.
    op.create_table(
        'calorie_burn_override',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('calories', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'date', name='uq_calorie_burn_override_user_date'),
    )
    op.create_index(op.f('ix_calorie_burn_override_user_id'), 'calorie_burn_override', ['user_id'], unique=False)
    op.create_index(op.f('ix_calorie_burn_override_date'), 'calorie_burn_override', ['date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_calorie_burn_override_date'), table_name='calorie_burn_override')
    op.drop_index(op.f('ix_calorie_burn_override_user_id'), table_name='calorie_burn_override')
    op.drop_table('calorie_burn_override')
    op.drop_index(op.f('ix_calorie_entry_entry_date'), table_name='calorie_entry')
    op.drop_index(op.f('ix_calorie_entry_user_id'), table_name='calorie_entry')
    op.drop_table('calorie_entry')
    op.drop_column('user', 'calorie_abandon_prompted_at')
    op.drop_column('user', 'calorie_reminder_hour_3')
    op.drop_column('user', 'calorie_reminder_hour_2')
    op.drop_column('user', 'calorie_reminder_hour_1')
    op.drop_column('user', 'daily_calorie_target')
