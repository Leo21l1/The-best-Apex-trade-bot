"""research studies (additive: new table only)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-18 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('research_studies',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('data_class', sa.String(length=16), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('trades', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_studies'))
    )
    op.create_index(op.f('ix_research_studies_symbol'), 'research_studies', ['symbol'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_research_studies_symbol'), table_name='research_studies')
    op.drop_table('research_studies')
