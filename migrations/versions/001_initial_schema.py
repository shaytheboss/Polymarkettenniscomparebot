"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'players',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('tour', sa.String(3), nullable=False),
        sa.Column('current_elo', sa.Float(), nullable=True),
        sa.Column('peak_elo', sa.Float(), nullable=True),
        sa.Column('elo_hard', sa.Float(), nullable=True),
        sa.Column('elo_clay', sa.Float(), nullable=True),
        sa.Column('elo_grass', sa.Float(), nullable=True),
        sa.Column('ranking', sa.Integer(), nullable=True),
        sa.Column('elo_updated_at', sa.Date(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index('ix_players_name', 'players', ['name'])

    op.create_table(
        'matches',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('external_id', sa.String(64), nullable=False),
        sa.Column('player1_id', sa.Integer(), sa.ForeignKey('players.id'), nullable=False),
        sa.Column('player2_id', sa.Integer(), sa.ForeignKey('players.id'), nullable=False),
        sa.Column('tour', sa.String(3), nullable=False),
        sa.Column('surface', sa.String(10), nullable=False),
        sa.Column('tournament', sa.String(120), nullable=True),
        sa.Column('round', sa.String(32), nullable=True),
        sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('status', sa.String(20), server_default='scheduled'),
        sa.Column('p1_sets', sa.Integer(), server_default='0'),
        sa.Column('p2_sets', sa.Integer(), server_default='0'),
        sa.Column('p1_games', sa.Integer(), server_default='0'),
        sa.Column('p2_games', sa.Integer(), server_default='0'),
        sa.Column('p1_pts', sa.Integer(), server_default='0'),
        sa.Column('p2_pts', sa.Integer(), server_default='0'),
        sa.Column('server', sa.Integer(), server_default='0'),
        sa.Column('in_tiebreak', sa.Boolean(), server_default='false'),
        sa.Column('score_text', sa.String(80), nullable=True),
        sa.Column('p1_elo_at_match', sa.Float(), nullable=True),
        sa.Column('p2_elo_at_match', sa.Float(), nullable=True),
        sa.Column('polymarket_condition_id', sa.String(80), nullable=True),
        sa.Column('last_poly_price_p1', sa.Float(), nullable=True),
        sa.Column('poly_updated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('winner_id', sa.Integer(), sa.ForeignKey('players.id'), nullable=True),
        sa.Column('final_score', sa.String(80), nullable=True),
        sa.Column('finished_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_id'),
    )
    op.create_index('ix_matches_external_id', 'matches', ['external_id'])
    op.create_index('ix_matches_polymarket_condition_id', 'matches', ['polymarket_condition_id'])

    op.create_table(
        'match_snapshots',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('match_id', sa.Integer(), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('p1_sets', sa.Integer()),
        sa.Column('p2_sets', sa.Integer()),
        sa.Column('p1_games', sa.Integer()),
        sa.Column('p2_games', sa.Integer()),
        sa.Column('p1_pts', sa.Integer()),
        sa.Column('p2_pts', sa.Integer()),
        sa.Column('server', sa.Integer()),
        sa.Column('in_tiebreak', sa.Boolean(), server_default='false'),
        sa.Column('table_prob_p1', sa.Float()),
        sa.Column('markov_prob_p1', sa.Float()),
        sa.Column('consensus_prob_p1', sa.Float()),
        sa.Column('model_agreement', sa.Float()),
        sa.Column('poly_price_p1', sa.Float(), nullable=True),
        sa.Column('edge_consensus', sa.Float(), nullable=True),
        sa.Column('raw_data', JSONB(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_match_snapshots_match_id', 'match_snapshots', ['match_id'])

    op.create_table(
        'opportunities',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('match_id', sa.Integer(), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('detected_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('back_player', sa.Integer(), nullable=False),
        sa.Column('back_player_name', sa.String(120), nullable=False),
        sa.Column('table_prob', sa.Float(), nullable=False),
        sa.Column('markov_prob', sa.Float(), nullable=False),
        sa.Column('consensus_prob', sa.Float(), nullable=False),
        sa.Column('poly_price', sa.Float(), nullable=False),
        sa.Column('edge_pp', sa.Float(), nullable=False),
        sa.Column('model_agreement', sa.Float(), nullable=False),
        sa.Column('score_text', sa.String(80), nullable=True),
        sa.Column('p1_sets', sa.Integer()),
        sa.Column('p2_sets', sa.Integer()),
        sa.Column('p1_games', sa.Integer()),
        sa.Column('p2_games', sa.Integer()),
        sa.Column('edge_category', sa.String(20), nullable=True),
        sa.Column('alert_sent', sa.Boolean(), server_default='false'),
        sa.Column('alert_sent_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('resolved', sa.Boolean(), server_default='false'),
        sa.Column('outcome', sa.String(10), nullable=True),
        sa.Column('resolved_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('pnl_units', sa.Float(), nullable=True),
        sa.Column('extra', JSONB(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_opportunities_match_id', 'opportunities', ['match_id'])

    op.create_table(
        'telegram_users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(80), nullable=True),
        sa.Column('min_edge_pp', sa.Integer(), server_default='5'),
        sa.Column('min_model_agreement', sa.Integer(), server_default='15'),
        sa.Column('active', sa.Boolean(), server_default='true'),
        sa.Column('tours_watched', ARRAY(sa.String()), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chat_id'),
    )

    op.create_table(
        'alerts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('opportunity_id', sa.Integer(), sa.ForeignKey('opportunities.id'), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('telegram_users.id'), nullable=True),
        sa.Column('sent_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('message_text', sa.String(2000), nullable=True),
        sa.Column('telegram_message_id', sa.BigInteger(), nullable=True),
        sa.Column('alert_type', sa.String(30), server_default='OPPORTUNITY'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'bot_settings',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('value', sa.String(512), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('alerts')
    op.drop_table('telegram_users')
    op.drop_table('opportunities')
    op.drop_table('match_snapshots')
    op.drop_table('matches')
    op.drop_table('players')
    op.drop_table('bot_settings')
