"""add team odds + weather counts to live_cycle_runs

Revision ID: e1f2a3b4c5d6
Revises: 03b8f763abb2
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = '03b8f763abb2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('live_cycle_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('team_odds_quotes_added', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('weather_snapshots_added', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('live_cycle_runs', schema=None) as batch_op:
        batch_op.drop_column('weather_snapshots_added')
        batch_op.drop_column('team_odds_quotes_added')
