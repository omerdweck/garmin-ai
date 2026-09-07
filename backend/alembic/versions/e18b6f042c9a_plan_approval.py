"""training plans await approval before entering tracking

Revision ID: e18b6f042c9a
Revises: d92f4c81a706
Create Date: 2026-09-07 14:30:00.000000

Existing plans are backfilled active: they were built under the old rule
where saving meant adopting, and the user is following them right now.

The unique constraint gains is_active so an approved plan and a proposal for
the same discipline can coexist - without that, drafting a new running plan
would collide with the running plan the user is currently following, which is
exactly the situation this feature creates.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e18b6f042c9a'
down_revision: Union[str, None] = 'd92f4c81a706'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('training_plan', sa.Column('is_active', sa.Boolean(), nullable=True))
    op.execute("UPDATE training_plan SET is_active = true WHERE is_active IS NULL")
    op.alter_column('training_plan', 'is_active', nullable=False)
    op.create_index(op.f('ix_training_plan_is_active'), 'training_plan', ['is_active'], unique=False)

    op.drop_constraint('uq_training_plan_user_discipline', 'training_plan', type_='unique')
    op.create_unique_constraint(
        'uq_training_plan_user_discipline_active',
        'training_plan',
        ['user_id', 'discipline', 'is_active'],
    )


def downgrade() -> None:
    # Proposals have no place in a schema that cannot express them, and they
    # would break the narrower constraint being restored.
    op.execute("DELETE FROM plan_session WHERE plan_id IN (SELECT id FROM training_plan WHERE is_active = false)")
    op.execute("DELETE FROM training_plan WHERE is_active = false")
    op.drop_constraint('uq_training_plan_user_discipline_active', 'training_plan', type_='unique')
    op.create_unique_constraint(
        'uq_training_plan_user_discipline', 'training_plan', ['user_id', 'discipline']
    )
    op.drop_index(op.f('ix_training_plan_is_active'), table_name='training_plan')
    op.drop_column('training_plan', 'is_active')
