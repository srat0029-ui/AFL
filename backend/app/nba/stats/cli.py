"""Frozen Stat Model V1 predictions.

    python -m app.nba.stats.cli freeze            # freeze what is due now; exit 1 if a snapshot failed
    python -m app.nba.stats.cli freeze --dry-run  # compute and measure; write nothing
    python -m app.nba.stats.cli evaluate          # score frozen rows against final box scores (read-only)
"""

import argparse
import json
import sys

from app.database import SessionLocal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.nba.stats.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("freeze", help="freeze predictions for V1.5 snapshots whose window is open")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("evaluate", help="score frozen predictions (read-only)")
    args = parser.parse_args(argv)

    from app.nba.stats.prospective import evaluate_frozen, freeze_due

    if args.command == "evaluate":
        with SessionLocal() as db:
            print(json.dumps(evaluate_frozen(db), indent=1, default=str))
        return 0

    from app.nba.outlooks.ops import measured

    with measured("stat freeze" + (" dry run" if args.dry_run else "")):
        with SessionLocal() as db:
            result = freeze_due(db, dry_run=args.dry_run)
    print(result.summary())
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
