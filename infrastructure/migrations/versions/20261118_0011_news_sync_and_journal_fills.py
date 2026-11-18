"""news calendar values, source sync state, journal fills (additive only)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-18 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('economic_events', sa.Column('forecast', sa.String(length=64), nullable=True))
    op.add_column('economic_events', sa.Column('previous', sa.String(length=64), nullable=True))
    op.add_column('economic_events', sa.Column('actual', sa.String(length=64), nullable=True))
    op.create_table('source_sync_state',
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failures', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_error', sa.String(length=300), nullable=True),
    sa.Column('events', sa.Integer(), server_default='0', nullable=False),
    sa.PrimaryKeyConstraint('source', name=op.f('pk_source_sync_state'))
    )
    op.add_column('journal_trades', sa.Column('fills', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('journal_trades', 'fills')
    op.drop_table('source_sync_state')
    op.drop_column('economic_events', 'actual')
    op.drop_column('economic_events', 'previous')
    op.drop_column('economic_events', 'forecast')
