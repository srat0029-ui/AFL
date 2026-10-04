"""Frozen V1.5 outlook snapshots.

    python -m app.nba.outlooks.cli snapshot                       # produce every due snapshot now
    python -m app.nba.outlooks.cli snapshot --labels T-24h,T-1h   # custom timings
    python -m app.nba.outlooks.cli snapshot --dry-run             # compute and print, write nothing
"""

import argparse
import sys
from datetime import timedelta

from app.database import SessionLocal
from app.nba.outlooks.snapshots import DEFAULT_SNAPSHOTS, DEFAULT_TOLERANCE, parse_specs, run_due_snapshots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.nba.outlooks.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("snapshot", help="freeze every snapshot that is due now")
    p.add_argument("--labels", default=",".join(s.label for s in DEFAULT_SNAPSHOTS), help="comma-separated, e.g. T-24h,T-4h,T-1h,T-30m")
    p.add_argument("--tolerance-minutes", type=float, default=DEFAULT_TOLERANCE.total_seconds() / 60)
    p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        result = run_due_snapshots(db, specs=parse_specs(args.labels), tolerance=timedelta(minutes=args.tolerance_minutes), dry_run=args.dry_run)
    print(result.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
