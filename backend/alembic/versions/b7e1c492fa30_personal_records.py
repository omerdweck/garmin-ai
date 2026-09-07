"""personal records

Revision ID: b7e1c492fa30
Revises: a3d5f8207e91
Create Date: 2026-09-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'b7e1c492fa30'
down_revision: Union[str, None] = 'a3d5f8207e91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'personal_record',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('record_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('achieved_at', sa.Date(), nullable=True),
        sa.Column('garmin_activity_id', sa.BigInteger(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
        # One row per record type per user: the row *is* the current best,
        # and overwriting it in place is what makes the previous value
        # available to compare against exactly once.
        sa.UniqueConstraint('user_id', 'record_type', name='uq_personal_record_user_type'),
    )
    op.create_index(op.f('ix_personal_record_user_id'), 'personal_record', ['user_id'], unique=False)
    op.create_index(op.f('ix_personal_record_record_type'), 'personal_record', ['record_type'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_personal_record_record_type'), table_name='personal_record')
    op.drop_index(op.f('ix_personal_record_user_id'), table_name='personal_record')
    op.drop_table('personal_record')
