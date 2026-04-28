"""Add polymarket_token_id to matches for CLOB price fetching

Revision ID: 005
Revises: 004
Create Date: 2025-04-28
"""
from alembic import op
import sqlalchemy as sa

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('matches', sa.Column('polymarket_token_id', sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column('matches', 'polymarket_token_id')
