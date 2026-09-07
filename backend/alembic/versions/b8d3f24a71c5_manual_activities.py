"""manual activities: nullable garmin id and a source column

Revision ID: b8d3f24a71c5
Revises: a91c7e50d284
Create Date: 2026-09-07 09:30:00.000000

Existing rows are all from Garmin, so source is backfilled to 'garmin'
before the column becomes NOT NULL - a default alone would have left the
existing rows NULL and quietly broken every query that filters on it.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # needed for sqlmodel.sql.sqltypes.AutoString - autogenerate doesn't add this import by itself


# revision identifiers, used by Alembic.
revision: str = 'b8d3f24a71c5'
down_revision: Union[str, None] = 'a91c7e50d284'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A manually entered workout has no Garmin id by definition. Postgres
    # permits any number of NULLs in a unique index, so the existing
    # (user_id, garmin_activity_id) constraint keeps making re-syncs
    # idempotent while placing no limit on manual rows.
    op.alter_column('activity', 'garmin_activity_id',
                    existing_type=sa.BigInteger(), nullable=True)

    op.add_column('activity', sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.execute("UPDATE activity SET source = 'garmin' WHERE source IS NULL")
    op.alter_column('activity', 'source', nullable=False)
    op.create_index(op.f('ix_activity_source'), 'activity', ['source'], unique=False)


def downgrade() -> None:
    # Manual rows have no Garmin id, so narrowing the column back would fail
    # on them - they have to go first, which is exactly the data loss this
    # migration's forward direction was designed to allow.
    op.execute("DELETE FROM activity WHERE source = 'manual'")
    op.drop_index(op.f('ix_activity_source'), table_name='activity')
    op.drop_column('activity', 'source')
    op.alter_column('activity', 'garmin_activity_id',
                    existing_type=sa.BigInteger(), nullable=False)
