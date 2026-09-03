"""Deterministic, minimal production seed: copies exactly the 17 approved
MUST-SEED tables (historical reference/model data) from a local SQLite dev
DB into a target PostgreSQL database, using the application's own
SQLAlchemy models on both sides - so JSON/DateTime/Boolean type conversion
is handled by SQLAlchemy's type system identically in both directions,
never hand-rolled.

Deliberately excludes every prospective/operational table (see
PROTECTED_MODELS below) and every local-only table (PlacedBet, local
PlayerPropMarket/OddsQuote history, WeeklyShortlistSnapshot, etc.). See
docs/DEPLOYMENT.md's "Production seeding" section for the full
classification and rationale, and the Phase 3 seed report this script
implements.

Safety guards (all enforced before a single row is written, except the
post-import verification which runs before the commit):
  - source must be a SQLite database; target must be PostgreSQL.
  - source and target must not resolve to the same database.
  - target PostgreSQL major version must be exactly 18.
  - target's current Alembic revision must equal the real migration head
    (computed from alembic/versions/, never hardcoded, so this never goes
    stale as new migrations are added).
  - every one of the 17 seed tables must be empty on the target.
  - every protected prospective/operational table must also be empty on
    the target - this script refuses to run anywhere close to a database
    that already has real operational history.
  - matches are filtered to status='completed' only (a locally scheduled-
    but-not-yet-played fixture is stale dev state, not a genuine current
    AFL fixture - production's own first live cycle refreshes real
    upcoming fixtures itself).
  - the manifest (expected table -> row count, plus data-cutoff/model
    facts) is built from the source BEFORE any write happens.
  - the entire import is one explicit transaction.
  - after inserting, this script automatically re-verifies: target counts
    match the manifest exactly, every table's identity sequence is ahead
    of its max id, and every protected table is still exactly zero. Any
    failure here rolls back the whole transaction - a partially-verified
    seed is never committed.
  - source/target connection strings are never printed; only
    `render_as_string(hide_password=True)` forms are ever shown.
"""

import argparse
import sys
from datetime import timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import (
    AnomalyAlertSnapshot,
    AnomalyCaseFollowUp,
    AnomalyCaseRecord,
    AnomalyCaseSnapshot,
    ApiUsageRecord,
    ExpectedLineup,
    GoalModelRun,
    GoalModelValidationMetric,
    LiveCycleRun,
    Match,
    MatchContextItem,
    MatchStatus,
    ModelPromotionEvent,
    ModelRun,
    ModelValidationMetric,
    ModelValueObservation,
    Player,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PlayerMatchStat,
    PlayerModelRun,
    PlayerModelValidationMetric,
    PricingSnapshot,
    PropMarketObservation,
    Round,
    Season,
    SgmDependenceCoefficient,
    SgmPriceSnapshot,
    SgmSnapshotLeg,
    Sport,
    Team,
    TeamMatchStat,
    Venue,
    VenueWeatherSnapshot,
    WeeklyShortlistSnapshot,
    WeeklyShortlistSnapshotItem,
)

REQUIRED_POSTGRES_MAJOR = 18

# Dependency order - every table appears after every table it references.
SEED_MODELS = [
    Sport,
    Season,
    Team,
    Venue,
    Round,
    Match,
    TeamMatchStat,
    Player,
    PlayerMatchStat,
    ModelRun,
    ModelValidationMetric,
    PlayerModelRun,
    PlayerModelValidationMetric,
    GoalModelRun,
    GoalModelValidationMetric,
    ModelPromotionEvent,
    SgmDependenceCoefficient,
]
assert len(SEED_MODELS) == 17

# The full audited set of prospective/operational tables that must remain
# empty forever until genuine production operation writes to them - not
# merely the original 7, per the Phase 3 report's extended audit.
PROTECTED_MODELS = [
    PricingSnapshot,
    SgmPriceSnapshot,
    SgmSnapshotLeg,
    PropMarketObservation,
    ModelValueObservation,
    LiveCycleRun,
    ApiUsageRecord,
    AnomalyCaseRecord,
    AnomalyAlertSnapshot,
    AnomalyCaseSnapshot,
    AnomalyCaseFollowUp,
    ExpectedLineup,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    VenueWeatherSnapshot,
    WeeklyShortlistSnapshot,
    WeeklyShortlistSnapshotItem,
    MatchContextItem,
]
assert len(PROTECTED_MODELS) == 18

# A locally SCHEDULED-but-not-yet-played fixture is stale dev state (see
# module docstring) - the only row-level filter this seed needs (every
# other seeded table already only ever references COMPLETED matches;
# verified empirically during the dry run: zero orphaned
# team_match_stats/player_match_stats rows after this filter).
MODEL_FILTERS = {
    Match: lambda stmt: stmt.where(Match.status == MatchStatus.COMPLETED),
}


class SeedGuardError(Exception):
    """A pre- or post-condition required before this script may write to
    (or commit against) the target database was not satisfied."""


def _redacted(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=True)


def check_source_is_sqlite(engine: Engine) -> None:
    if engine.url.get_backend_name() != "sqlite":
        raise SeedGuardError(f"Source must be a SQLite database; got backend {engine.url.get_backend_name()!r}.")


def check_target_is_postgres(engine: Engine) -> None:
    if engine.url.get_backend_name() not in ("postgresql", "postgres"):
        raise SeedGuardError(f"Target must be PostgreSQL; got backend {engine.url.get_backend_name()!r}.")


def check_distinct_databases(source_engine: Engine, target_engine: Engine) -> None:
    # Different dialects can never be "the same database" in practice
    # (this script only ever moves sqlite -> postgresql), but comparing the
    # full rendered URL is a cheap, unconditional guard against a
    # copy-paste error that points both at the same value.
    if _redacted(source_engine) == _redacted(target_engine):
        raise SeedGuardError("Source and target resolve to the same database - refusing to run.")


def check_target_postgres_major(engine: Engine, expected: int = REQUIRED_POSTGRES_MAJOR) -> None:
    with engine.connect() as conn:
        version_string = conn.execute(text("SELECT version();")).scalar()
    import re

    m = re.search(r"PostgreSQL (\d+)", version_string or "")
    major = int(m.group(1)) if m else None
    if major != expected:
        raise SeedGuardError(f"Target PostgreSQL major version must be {expected}; got {major} ({version_string!r}).")


def _real_migration_head() -> str:
    """The true current head, computed from alembic/versions/ itself - never
    hardcoded, so this guard can't go stale the next time a migration is
    added."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise SeedGuardError(f"Expected exactly one Alembic head, found {heads!r} - migration history has diverged.")
    return heads[0]


def check_target_alembic_head(engine: Engine, expected_head: str) -> None:
    with engine.connect() as conn:
        try:
            current = conn.execute(text("SELECT version_num FROM alembic_version;")).scalar()
        except Exception as exc:  # noqa: BLE001
            raise SeedGuardError(f"Could not read target's alembic_version table: {exc}") from exc
    if current != expected_head:
        raise SeedGuardError(f"Target Alembic revision must be the current head {expected_head!r}; got {current!r}.")


def check_tables_empty(session: Session, model_classes: list, label: str) -> None:
    for model_cls in model_classes:
        n = session.scalar(select(func.count()).select_from(model_cls))
        if n:
            raise SeedGuardError(f"{label} table {model_cls.__tablename__!r} is not empty ({n} row(s)) - refusing to run.")


def _coerce_row_dict(model_cls, obj) -> dict:
    row = {}
    for col in model_cls.__table__.columns:
        value = getattr(obj, col.name)
        if hasattr(value, "tzinfo") and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        row[col.name] = value
    return row


def _filtered_select(model_cls):
    stmt = select(model_cls)
    if model_cls in MODEL_FILTERS:
        stmt = MODEL_FILTERS[model_cls](stmt)
    return stmt


def build_manifest(source_db: Session) -> dict:
    """Built entirely from the source, before any write to the target -
    the frozen expectation every later verification step is checked
    against."""
    manifest: dict = {"tables": {}}
    for model_cls in SEED_MODELS:
        n = source_db.scalar(select(func.count()).select_from(_filtered_select(model_cls).subquery()))
        manifest["tables"][model_cls.__tablename__] = n

    cutoff_row = source_db.execute(
        select(func.min(Match.scheduled_start), func.max(Match.scheduled_start)).where(Match.status == MatchStatus.COMPLETED)
    ).one()
    manifest["completed_match_date_range"] = [str(cutoff_row[0]), str(cutoff_row[1])]

    promoted_disposal = source_db.scalar(select(PlayerModelRun).where(PlayerModelRun.is_promoted.is_(True)))
    promoted_goal = source_db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
    manifest["promoted_disposal_model"] = promoted_disposal.model_name if promoted_disposal else None
    manifest["promoted_goal_model"] = promoted_goal.model_name if promoted_goal else None

    coeffs = source_db.scalars(select(SgmDependenceCoefficient)).all()
    manifest["sgm_dependence_coefficients"] = {c.market: {"slope": c.slope, "intercept": c.intercept, "n_observations": c.n_observations} for c in coeffs}

    return manifest


def verify_against_manifest(target_db: Session, manifest: dict) -> list[str]:
    """Returns a list of problems (empty = all good). Never raises itself -
    the caller decides whether to roll back."""
    problems = []
    for model_cls in SEED_MODELS:
        expected = manifest["tables"][model_cls.__tablename__]
        actual = target_db.scalar(select(func.count()).select_from(model_cls))
        if actual != expected:
            problems.append(f"count mismatch on {model_cls.__tablename__!r}: expected {expected}, got {actual}")

    for model_cls in SEED_MODELS:
        table_name = model_cls.__tablename__
        max_id = target_db.execute(text(f'SELECT MAX(id) FROM "{table_name}"')).scalar()
        if max_id is None:
            continue
        # Read-only: pg_sequences.last_value reflects what setval() above
        # set it to, without the side effect nextval() would have of
        # actually consuming a value just to check it.
        seq_name = target_db.execute(text(f"SELECT pg_get_serial_sequence('{table_name}', 'id')")).scalar()
        last_value = target_db.execute(text(f'SELECT last_value FROM {seq_name}')).scalar()
        if last_value < max_id:
            problems.append(f"sequence for {table_name!r} behind max id: max_id={max_id}, sequence last_value={last_value}")

    for model_cls in PROTECTED_MODELS:
        n = target_db.scalar(select(func.count()).select_from(model_cls))
        if n:
            problems.append(f"protected table {model_cls.__tablename__!r} is non-zero after import ({n} row(s))")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sqlite-path", required=True, help="Path to the local afl.db SQLite file")
    parser.add_argument("--target-url", required=True, help="SQLAlchemy URL for the target PostgreSQL database")
    args = parser.parse_args()

    source_engine = create_engine(f"sqlite:///{args.source_sqlite_path}")
    target_engine = create_engine(args.target_url)

    print(f"source: {_redacted(source_engine)}")
    print(f"target: {_redacted(target_engine)}")

    try:
        check_source_is_sqlite(source_engine)
        check_target_is_postgres(target_engine)
        check_distinct_databases(source_engine, target_engine)
        check_target_postgres_major(target_engine)
        expected_head = _real_migration_head()
        check_target_alembic_head(target_engine, expected_head)
        print(f"alembic head verified: {expected_head}")
    except SeedGuardError as exc:
        print(f"ABORT (pre-flight guard): {exc}")
        return 1

    with Session(source_engine) as source_db, Session(target_engine) as target_db:
        try:
            check_tables_empty(target_db, SEED_MODELS, "seed")
            check_tables_empty(target_db, PROTECTED_MODELS, "protected")
        except SeedGuardError as exc:
            print(f"ABORT (emptiness guard): {exc}")
            return 1

        manifest = build_manifest(source_db)
        print("\n--- MANIFEST (built before any write) ---")
        for table, n in manifest["tables"].items():
            print(f"  {table}: {n}")
        print(f"  completed match date range: {manifest['completed_match_date_range']}")
        print(f"  promoted disposal model: {manifest['promoted_disposal_model']}")
        print(f"  promoted goal model: {manifest['promoted_goal_model']}")
        print(f"  sgm dependence coefficients: {manifest['sgm_dependence_coefficients']}")

        counts = {}
        try:
            for model_cls in SEED_MODELS:
                source_rows = source_db.scalars(_filtered_select(model_cls)).all()
                dict_rows = [_coerce_row_dict(model_cls, r) for r in source_rows]
                if dict_rows:
                    target_db.execute(model_cls.__table__.insert(), dict_rows)
                counts[model_cls.__tablename__] = len(dict_rows)
                print(f"seeded {model_cls.__tablename__}: {len(dict_rows)} row(s)")

            for model_cls in SEED_MODELS:
                table_name = model_cls.__tablename__
                target_db.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), "
                        f"(SELECT COUNT(*) FROM {table_name}) > 0)"
                    )
                )

            target_db.flush()
            problems = verify_against_manifest(target_db, manifest)
            if problems:
                raise SeedGuardError("post-import verification failed:\n  - " + "\n  - ".join(problems))
        except Exception:
            target_db.rollback()
            print("\nSeed FAILED verification and was rolled back - target database unchanged.")
            raise

        target_db.commit()
        print("\nSeed committed successfully - post-import verification passed.")
        print("Row counts:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
