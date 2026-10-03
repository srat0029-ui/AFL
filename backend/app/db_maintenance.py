"""Guarded schema migration for the hosted database, run by the manually
triggered `.github/workflows/db-migrate.yml`. Shared by every sport.

    python -m app.db_maintenance inventory --out before.json
    python -m app.db_maintenance upgrade --expect-current 75715f635e7a
    python -m app.db_maintenance inventory --out after.json
    python -m app.db_maintenance compare before.json after.json

The point is to make a production migration checkable rather than trusted:

- `inventory` records every table and its exact row count (read-only).
- `upgrade` refuses to do anything unless the database is at exactly the
  revision the operator said it would be, then runs `alembic upgrade head`
  and prints the revision before and after.
- `compare` fails if any table that existed before is missing afterwards
  or has a different row count - i.e. if the migration touched existing
  data - and lists the tables it created.

Migrations still never run automatically: this is only ever invoked by a
person dispatching that workflow with an explicit confirmation.
"""

import argparse
import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.database import SessionLocal, engine
from app.migration_status import ALEMBIC_INI, migration_status


def inventory() -> dict:
    tables = sorted(inspect(engine).get_table_names())
    counts = {}
    with engine.connect() as connection:
        for table in tables:
            counts[table] = connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
    db = SessionLocal()
    try:
        revision = migration_status(db).database_revision
    finally:
        db.close()
    return {"revision": revision, "row_counts": counts}


def compare(before: dict, after: dict) -> tuple[bool, list[str]]:
    """(ok, report lines). Not ok if any pre-existing table vanished or
    changed row count. `alembic_version` is ignored: it is supposed to change."""
    lines: list[str] = [f"revision: {before.get('revision')} -> {after.get('revision')}"]
    ok = True
    for table, count in sorted(before["row_counts"].items()):
        if table == "alembic_version":
            continue
        if table not in after["row_counts"]:
            ok = False
            lines.append(f"MISSING AFTER MIGRATION: {table} (had {count} rows)")
        elif after["row_counts"][table] != count:
            ok = False
            lines.append(f"ROW COUNT CHANGED: {table} {count} -> {after['row_counts'][table]}")
    unchanged = sum(1 for t in before["row_counts"] if t != "alembic_version" and after["row_counts"].get(t) == before["row_counts"][t])
    lines.append(f"pre-existing tables with identical row counts: {unchanged}")
    created = sorted(set(after["row_counts"]) - set(before["row_counts"]))
    lines.append(f"tables created: {len(created)}")
    lines += [f"  + {table} ({after['row_counts'][table]} rows)" for table in created]
    return ok, lines


def upgrade(expect_current: str, out=sys.stdout) -> int:
    db = SessionLocal()
    try:
        status = migration_status(db)
    finally:
        db.close()
    print(f"database revision: {status.database_revision}", file=out)
    print(f"code head:         {status.code_head}", file=out)
    if status.database_revision != expect_current:
        print(f"REFUSED: expected the database to be at {expect_current}, found {status.database_revision}. Nothing was changed.", file=out)
        return 2
    if status.is_current:
        print("already at head; nothing to do", file=out)
        return 0
    print("migrations to apply, oldest first:", file=out)
    for revision, description in status.pending:
        print(f"  {revision}  {description}", file=out)
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(Path(ALEMBIC_INI).parent / "alembic"))
    command.upgrade(config, "head")
    db = SessionLocal()
    try:
        after = migration_status(db)
    finally:
        db.close()
    print(f"database revision after upgrade: {after.database_revision}", file=out)
    return 0 if after.is_current else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.db_maintenance")
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--out", required=True)
    up = sub.add_parser("upgrade")
    up.add_argument("--expect-current", required=True, help="the revision the database must be at, or nothing is done")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("before")
    cmp_.add_argument("after")
    args = parser.parse_args(argv)

    if args.command == "inventory":
        snapshot = inventory()
        Path(args.out).write_text(json.dumps(snapshot, indent=1, sort_keys=True), encoding="utf-8")
        print(f"revision {snapshot['revision']}; {len(snapshot['row_counts'])} tables; {sum(snapshot['row_counts'].values())} rows in total")
        return 0
    if args.command == "upgrade":
        return upgrade(args.expect_current)
    ok, lines = compare(json.loads(Path(args.before).read_text(encoding="utf-8")), json.loads(Path(args.after).read_text(encoding="utf-8")))
    print("\n".join(lines))
    print("existing data unchanged" if ok else "EXISTING DATA CHANGED - investigate before doing anything else")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
