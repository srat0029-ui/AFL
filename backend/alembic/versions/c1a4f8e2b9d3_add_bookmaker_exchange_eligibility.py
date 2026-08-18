"""add bookmaker is_exchange + eligibility

Revision ID: c1a4f8e2b9d3
Revises: 5c2a90036505
Create Date: 2026-08-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a4f8e2b9d3'
down_revision: Union[str, None] = '5c2a90036505'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Section 4's evidence-backed rule (real data: The Odds API returns
# Betfair's exchange product under provider_key "betfair_ex_au", the only
# observed key containing this marker; every plain sportsbook key -
# "sportsbet", "tab", "ladbrokes_au", etc. - does not).
_EXCHANGE_KEY_MARKER = "_ex_"


def upgrade() -> None:
    with op.batch_alter_table('bookmakers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_exchange', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('eligibility', sa.String(length=24), nullable=False, server_default='included'))

    # Backfill classification for bookmakers already discovered from real
    # provider data before this migration existed.
    bookmakers = sa.table(
        'bookmakers',
        sa.column('id', sa.Integer),
        sa.column('provider_key', sa.String),
        sa.column('is_exchange', sa.Boolean),
        sa.column('eligibility', sa.String),
    )
    conn = op.get_bind()
    rows = conn.execute(sa.select(bookmakers.c.id, bookmakers.c.provider_key)).fetchall()
    for row in rows:
        if row.provider_key and _EXCHANGE_KEY_MARKER in row.provider_key:
            conn.execute(
                bookmakers.update()
                .where(bookmakers.c.id == row.id)
                .values(is_exchange=True, eligibility='informational_only')
            )


def downgrade() -> None:
    with op.batch_alter_table('bookmakers', schema=None) as batch_op:
        batch_op.drop_column('eligibility')
        batch_op.drop_column('is_exchange')
