"""Enrich opportunities table with Kelly, ELO gap, edge type, serve probs

Revision ID: 003
Revises: 002
Create Date: 2024-01-03
"""
from alembic import op
import sqlalchemy as sa

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Opportunities: add Kelly fraction, ELO info, edge type, serve probs
    op.add_column('opportunities', sa.Column('kelly_fraction', sa.Float(), nullable=True))
    op.add_column('opportunities', sa.Column('elo_gap', sa.Float(), nullable=True))
    op.add_column('opportunities', sa.Column('elo_band', sa.String(4), nullable=True))
    op.add_column('opportunities', sa.Column('edge_type', sa.String(40), nullable=True))
    op.add_column('opportunities', sa.Column('p1_serve_prob', sa.Float(), nullable=True))
    op.add_column('opportunities', sa.Column('p2_serve_prob', sa.Float(), nullable=True))
    op.add_column('opportunities', sa.Column('surface', sa.String(10), nullable=True))
    op.add_column('opportunities', sa.Column('tour', sa.String(3), nullable=True))

    # Match snapshots: add serve probs (from Markov calibration)
    op.add_column('match_snapshots', sa.Column('p1_serve_prob', sa.Float(), nullable=True))
    op.add_column('match_snapshots', sa.Column('p2_serve_prob', sa.Float(), nullable=True))
    op.add_column('match_snapshots', sa.Column('elo_gap', sa.Float(), nullable=True))

    # Matches: track ELO gap at match time for easier querying
    op.add_column('matches', sa.Column('elo_gap_at_match', sa.Float(), nullable=True))

    # Telegram users: add notification for resolved opportunities
    op.add_column('telegram_users', sa.Column('notify_resolved', sa.Boolean(), server_default='true'))


def downgrade() -> None:
    for col in ['kelly_fraction', 'elo_gap', 'elo_band', 'edge_type',
                'p1_serve_prob', 'p2_serve_prob', 'surface', 'tour']:
        op.drop_column('opportunities', col)
    for col in ['p1_serve_prob', 'p2_serve_prob', 'elo_gap']:
        op.drop_column('match_snapshots', col)
    op.drop_column('matches', 'elo_gap_at_match')
    op.drop_column('telegram_users', 'notify_resolved')
