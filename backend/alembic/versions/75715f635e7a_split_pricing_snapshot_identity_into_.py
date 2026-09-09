"""split pricing snapshot identity into team and player partial unique indexes

Revision ID: 75715f635e7a
Revises: d633f77191dc
Create Date: 2026-09-08 17:35:46.393914

A real production forensic review (LiveCycleRun id=4's audit) found the
single uq_pricing_snapshot_identity constraint omitted player_id, and
PostgreSQL's standard NULL-distinct uniqueness semantics meant it provided
no real protection for player rows anyway, since line_value is always NULL
there. A full production data audit confirmed the existing 842 rows are
already clean under the corrected per-family keys (every one of 18
(match, model_version, market_type, threshold) player groups had exactly
one row per eligible player, and every team row was already unique under
its own key) - this migration replaces the constraint, it does not repair
any data, and the preflight check below verifies that cleanliness rather
than assuming it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '75715f635e7a'
down_revision: Union[str, None] = 'd633f77191dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_CONSTRAINT = "uq_pricing_snapshot_identity"
_TEAM_INDEX = "uq_pricing_snapshot_team_identity"
_PLAYER_INDEX = "uq_pricing_snapshot_player_identity"


def _check_no_existing_violations() -> None:
    """Defensive preflight, same discipline as 8316cece5ae7's venue-name
    consolidation - confirms BOTH new partial keys are already free of
    duplicates before touching the schema at all, so index creation can
    never fail partway through and leave the table half-migrated. Unlike
    that venue migration, this one deliberately does NOT merge or delete
    anything if it finds a violation: PricingSnapshot's bug undercounts
    (missing rows), it does not duplicate, so a genuine violation here
    would be a real, unexpected anomaly worth a human looking at rather
    than an automated pick-a-winner resolution - especially since two real
    snapshot rows could already have been independently settled with
    different outcomes. Fails loudly and makes no destructive changes if
    anything is found."""
    conn = op.get_bind()
    snaps = sa.table(
        "pricing_snapshots",
        sa.column("match_id", sa.Integer), sa.column("player_id", sa.Integer),
        sa.column("market_type", sa.String), sa.column("selection", sa.String),
        sa.column("threshold", sa.Float), sa.column("line_value", sa.Float), sa.column("model_version", sa.String),
    )
    rows = conn.execute(sa.select(
        snaps.c.match_id, snaps.c.player_id, snaps.c.market_type, snaps.c.selection,
        snaps.c.threshold, snaps.c.line_value, snaps.c.model_version,
    )).fetchall()

    team_keys: dict[tuple, int] = {}
    player_keys: dict[tuple, int] = {}
    violations: list[str] = []
    for match_id, player_id, market_type, selection, threshold, line_value, model_version in rows:
        if player_id is None:
            key = (match_id, market_type, selection, line_value, model_version)
            team_keys[key] = team_keys.get(key, 0) + 1
            if team_keys[key] == 2:
                violations.append(
                    f"duplicate TEAM identity: match_id={match_id} market_type={market_type} "
                    f"selection={selection} line_value={line_value} model_version={model_version}"
                )
        else:
            key = (match_id, player_id, market_type, selection, threshold, model_version)
            player_keys[key] = player_keys.get(key, 0) + 1
            if player_keys[key] == 2:
                violations.append(
                    f"duplicate PLAYER identity: match_id={match_id} player_id={player_id} "
                    f"market_type={market_type} selection={selection} threshold={threshold} model_version={model_version}"
                )

    if violations:
        raise RuntimeError(
            "pricing_snapshots preflight check found rows that would violate the new partial unique "
            f"indexes - refusing to migrate without manual review ({len(violations)} violation(s)):\n"
            + "\n".join(violations[:20])
            + ("\n... (truncated)" if len(violations) > 20 else "")
        )
    print(f"  pricing_snapshots identity preflight check: {len(rows)} row(s) inspected, no violations found")


def upgrade() -> None:
    # Order matters: validate, then ADD the new (stricter) protection,
    # THEN remove the old (weaker) one - the table is never left with less
    # protection than it started with at any point in this migration.
    _check_no_existing_violations()

    # line_value is used raw, not wrapped in a sentinel expression: h2h team
    # snapshots always have line_value=NULL (only line/total carry a real
    # number), so a plain NULL column in a STANDARD unique index would
    # reproduce the exact NULL-distinct bug this migration exists to fix,
    # just for h2h instead of player rows - caught by this project's own
    # required duplicate-team-snapshot test before this shipped. Rather than
    # embedding a magic sentinel (e.g. COALESCE(line_value, -999999)) in the
    # canonical identity, this uses PostgreSQL's native `NULLS NOT DISTINCT`
    # (Postgres 15+; both this project's supported versions, 16 and 18, have
    # it) so two NULL line_values correctly collide using the database's own
    # expression of that intent. SQLAlchemy has no sqlite equivalent for
    # this, so on SQLite this index is a plain (weaker) unique index for
    # schema-shape purposes only - the real guarantee is PostgreSQL-only.
    # See the model docstring for the full explanation.
    op.create_index(
        _TEAM_INDEX, "pricing_snapshots",
        ["match_id", "market_type", "selection", "line_value", "model_version"],
        unique=True,
        postgresql_where=sa.text("player_id IS NULL"),
        sqlite_where=sa.text("player_id IS NULL"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        _PLAYER_INDEX, "pricing_snapshots",
        ["match_id", "player_id", "market_type", "selection", "threshold", "model_version"],
        unique=True,
        postgresql_where=sa.text("player_id IS NOT NULL"),
        sqlite_where=sa.text("player_id IS NOT NULL"),
    )

    with op.batch_alter_table("pricing_snapshots", schema=None) as batch_op:
        batch_op.drop_constraint(_OLD_CONSTRAINT, type_="unique")


def downgrade() -> None:
    # Restores the ORIGINAL schema shape, not the corrected semantics -
    # the old constraint's NULL-distinct weakness for player rows
    # (line_value is always NULL there) is a known, accepted property of
    # this downgrade path, not something this function attempts to fix.
    # Order matters here too: add the old constraint back BEFORE dropping
    # the new indexes, so there is always at least one layer of DB-level
    # protection in place. No data is deleted or modified in either
    # direction.
    with op.batch_alter_table("pricing_snapshots", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            _OLD_CONSTRAINT,
            ["match_id", "market_type", "selection", "threshold", "line_value", "model_version"],
        )

    op.drop_index(_PLAYER_INDEX, table_name="pricing_snapshots")
    op.drop_index(_TEAM_INDEX, table_name="pricing_snapshots")
