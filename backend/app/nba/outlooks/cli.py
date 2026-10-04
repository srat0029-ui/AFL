"""Frozen V1.5 outlook snapshots and the canonical serving bundle.

    python -m app.nba.outlooks.cli snapshot                       # produce every due snapshot now
    python -m app.nba.outlooks.cli snapshot --labels T-24h,T-1h   # custom timings
    python -m app.nba.outlooks.cli snapshot --dry-run             # compute and print, write nothing
    python -m app.nba.outlooks.cli verify-serving-models          # bundle present, unmodified, golden predictions (exit 1 if not)
    python -m app.nba.outlooks.cli import-serving-models          # write the canonical serving runs into this DB's registry
"""

import argparse
import sys

from app.database import SessionLocal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.nba.outlooks.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("snapshot", help="freeze every snapshot that is due now")
    p.add_argument("--labels", default=None, help="comma-separated, e.g. T-24h,T-4h,T-1h,T-30m (default: all four)")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("verify-serving-models", help="verify the bundled canonical models (no database needed)")
    sub.add_parser("import-serving-models", help="import the canonical serving runs into this database's registry")
    args = parser.parse_args(argv)

    if args.command == "verify-serving-models":
        from app.nba.serving_bundle import verify_bundle

        result = verify_bundle()
        print("\n".join(result.lines))
        return 0 if result.ok else 1

    if args.command == "import-serving-models":
        from app.nba.serving_bundle import import_bundle

        with SessionLocal() as db:
            print("\n".join(import_bundle(db)))
        return 0

    from app.nba.outlooks.snapshots import DEFAULT_SNAPSHOTS, parse_specs, run_due_snapshots

    specs = parse_specs(args.labels) if args.labels else DEFAULT_SNAPSHOTS
    with SessionLocal() as db:
        result = run_due_snapshots(db, specs=specs, dry_run=args.dry_run)
    print(result.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
