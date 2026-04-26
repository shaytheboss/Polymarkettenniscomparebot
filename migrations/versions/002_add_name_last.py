"""Add name_last index column to players

Revision ID: 002
Revises: 001
Create Date: 2024-01-02
"""
from alembic import op
import sqlalchemy as sa

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('players', sa.Column('name_last', sa.String(60), nullable=True))
    op.create_index('ix_players_name_last', 'players', ['name_last'])

    # Backfill: set name_last for existing rows using SQL string split
    op.execute("""
        UPDATE players
        SET name_last = lower(
            regexp_replace(
                split_part(
                    regexp_replace(name, '[^a-zA-Z ]', '', 'g'),
                    ' ',
                    length(regexp_replace(name, '[^ ]', '', 'g')) + 1
                ),
                '[^a-z]', '', 'g'
            )
        )
        WHERE name_last IS NULL
    """)


def downgrade() -> None:
    op.drop_index('ix_players_name_last', 'players')
    op.drop_column('players', 'name_last')
