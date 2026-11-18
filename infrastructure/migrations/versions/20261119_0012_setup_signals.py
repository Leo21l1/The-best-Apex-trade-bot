"""setup signal history (additive: new table only)

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-19 09:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('setup_signals',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('key', sa.String(length=200), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('contract', sa.String(length=16), nullable=True),
    sa.Column('data_class', sa.String(length=16), nullable=False),
    sa.Column('strategy_id', sa.String(length=32), nullable=False),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('direction', sa.String(length=8), nullable=False),
    sa.Column('score', sa.Integer(), nullable=False),
    sa.Column('qualified', sa.Boolean(), nullable=False),
    sa.Column('evidence', sa.String(length=32), nullable=False),
    sa.Column('trigger_bar', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('entry', sa.Float(), nullable=False),
    sa.Column('entry_low', sa.Float(), nullable=False),
    sa.Column('entry_high', sa.Float(), nullable=False),
    sa.Column('stop', sa.Float(), nullable=False),
    sa.Column('tp1', sa.Float(), nullable=False),
    sa.Column('tp2', sa.Float(), nullable=True),
    sa.Column('net_rr_tp1', sa.Float(), nullable=True),
    sa.Column('net_risk_usd', sa.Float(), nullable=True),
    sa.Column('net_reward_usd', sa.Float(), nullable=True),
    sa.Column('original', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('status_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status_reason', sa.String(length=200), nullable=True),
    sa.Column('filled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('outcome', sa.String(length=16), nullable=True),
    sa.Column('outcome_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('outcome_r', sa.Float(), nullable=True),
    sa.Column('outcome_usd', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_setup_signals')),
    sa.UniqueConstraint('key', name=op.f('uq_setup_signals_key'))
    )
    op.create_index('ix_setup_signals_symbol_confirmed', 'setup_signals', ['symbol', 'confirmed_at'], unique=False)
    op.create_index(op.f('ix_setup_signals_symbol'), 'setup_signals', ['symbol'], unique=False)
    op.create_index(op.f('ix_setup_signals_status'), 'setup_signals', ['status'], unique=False)


def downgrade() -> None:
    op.drop_table('setup_signals')
