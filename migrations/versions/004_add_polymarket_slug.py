"""Add polymarket_slug to matches for direct market URLs

Revision ID: 004
Revises: 003
Create Date: 2025-04-28
"""
from alembic import op
import sqlalchemy as sa

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('matches', sa.Column('polymarket_slug', sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column('matches', 'polymarket_slug')
