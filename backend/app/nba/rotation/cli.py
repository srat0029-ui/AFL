"""NBA rotation layer (V1.5) command line.

    python -m app.nba.rotation.cli evaluate              # full participation / rotation / reconciliation experiment
    python -m app.nba.rotation.cli predict-upcoming      # freeze rotation outlooks for upcoming games
"""

import argparse
import sys

from app.database import SessionLocal


def _evaluate(args) -> int:
    from app.nba.rotation.experiment import run_experiment

    with SessionLocal() as db:
        run_experiment(db, check_rows=not args.skip_lookahead_check)
    return 0


def _predict(args) -> int:
    from app.nba.rotation.prospective import predict_rotation_upcoming

    with SessionLocal() as db:
        print(predict_rotation_upcoming(db, hours_ahead=args.hours_ahead, dry_run=args.dry_run).summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.nba.rotation.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("evaluate", help="run the full V1.5 experiment and record it")
    p.add_argument("--skip-lookahead-check", action="store_true")
    p.set_defaults(func=_evaluate)
    p = sub.add_parser("predict-upcoming", help="freeze rotation outlooks for games tipping off soon")
    p.add_argument("--hours-ahead", type=float, default=36.0)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_predict)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
