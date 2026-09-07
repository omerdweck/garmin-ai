"""meal_plan and meal_option

Revision ID: d92f4c81a706
Revises: c4a71e9b3d58
Create Date: 2026-09-07 12:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # needed for sqlmodel.sql.sqltypes.AutoString - autogenerate doesn't add this import by itself


# revision identifiers, used by Alembic.
revision: str = 'd92f4c81a706'
down_revision: Union[str, None] = 'c4a71e9b3d58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'meal_plan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('approach', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('target_calories', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
        # One plan per user - there is only one way to eat, unlike training
        # which is one plan per discipline.
        sa.UniqueConstraint('user_id', name='uq_meal_plan_user'),
    )
    op.create_index(op.f('ix_meal_plan_user_id'), 'meal_plan', ['user_id'], unique=False)

    op.create_table(
        'meal_option',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('meal_slot', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('calories', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['plan_id'], ['meal_plan.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_meal_option_user_id'), 'meal_option', ['user_id'], unique=False)
    op.create_index(op.f('ix_meal_option_plan_id'), 'meal_option', ['plan_id'], unique=False)
    op.create_index(op.f('ix_meal_option_meal_slot'), 'meal_option', ['meal_slot'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_meal_option_meal_slot'), table_name='meal_option')
    op.drop_index(op.f('ix_meal_option_plan_id'), table_name='meal_option')
    op.drop_index(op.f('ix_meal_option_user_id'), table_name='meal_option')
    op.drop_table('meal_option')
    op.drop_index(op.f('ix_meal_plan_user_id'), table_name='meal_plan')
    op.drop_table('meal_plan')
