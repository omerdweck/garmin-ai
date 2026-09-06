"""join_request table

Revision ID: f2b4a67c9e13
Revises: e5c81f3d7b26
Create Date: 2026-09-06 12:30:00.000000

Replaces the shared invite code, which is dropped from settings in the same
change. Nothing to migrate: the code was a config value, never data.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # needed for sqlmodel.sql.sqltypes.AutoString - autogenerate doesn't add this import by itself


# revision identifiers, used by Alembic.
revision: str = 'f2b4a67c9e13'
down_revision: Union[str, None] = 'e5c81f3d7b26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'join_request',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('telegram_chat_id', sa.BigInteger(), nullable=False),
        sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('telegram_username', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('requested_at', sa.DateTime(), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    # Unique: one standing request per chat, so a second /start updates the
    # row instead of queueing a duplicate the owner has to answer twice.
    op.create_index(op.f('ix_join_request_telegram_chat_id'), 'join_request', ['telegram_chat_id'], unique=True)
    op.create_index(op.f('ix_join_request_status'), 'join_request', ['status'], unique=False)

    # user.telegram_chat_id was INTEGER, which tops out at 2,147,483,647.
    # Telegram ids have outgrown that: accounts created in recent years get
    # values like 6123456789. The first friend with a newer account would
    # have failed to register with an overflow error saying nothing useful.
    # Widening is safe and preserves existing values.
    op.alter_column(
        'user',
        'telegram_chat_id',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Narrowing back would fail on any id that needed the extra width, which
    # is the whole point - so this is only safe while no such row exists.
    op.alter_column(
        'user',
        'telegram_chat_id',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.drop_index(op.f('ix_join_request_status'), table_name='join_request')
    op.drop_index(op.f('ix_join_request_telegram_chat_id'), table_name='join_request')
    op.drop_table('join_request')
