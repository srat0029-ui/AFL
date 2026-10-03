"""Expected Minutes Model V1 command line.

    python -m app.nba.minutes.cli evaluate          # the full pre-registered experiment
    python -m app.nba.minutes.cli runs              # list recorded model runs
    python -m app.nba.minutes.cli predict-upcoming  # freeze predictions for upcoming games
"""

import argparse
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models.nba import NbaMinutesModelRun


def _evaluate(args) -> int:
    from app.nba.minutes.experiment import run_experiment

    with SessionLocal() as db:
        run_experiment(db, check_rows=not args.skip_lookahead_check)
    return 0


def _runs(args) -> int:
    with SessionLocal() as db:
        for run in db.scalars(select(NbaMinutesModelRun).order_by(NbaMinutesModelRun.id)).all():
            m = run.metrics.get("walk_forward_pooled_test") or {}
            mae = f"{m['mae']:.3f}" if "mae" in m else "-"
            print(f"{run.id:>4}  {run.purpose:<10} {run.model_name:<20} {run.model_version:<14} test MAE {mae:>6}  {run.run_key}")
    return 0


def _predict_upcoming(args) -> int:
    from app.nba.minutes.prospective import predict_upcoming

    with SessionLocal() as db:
        result = predict_upcoming(db, hours_ahead=args.hours_ahead, model_run_id=args.model_run_id, dry_run=args.dry_run)
    print(result.summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.nba.minutes.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("evaluate", help="run the full Model V1 experiment and record it")
    p.add_argument("--skip-lookahead-check", action="store_true")
    p.set_defaults(func=_evaluate)
    sub.add_parser("runs", help="list recorded minutes-model runs").set_defaults(func=_runs)
    p = sub.add_parser("predict-upcoming", help="freeze expected-minutes predictions for games tipping off soon")
    p.add_argument("--hours-ahead", type=float, default=36.0)
    p.add_argument("--model-run-id", type=int, default=None, help="serving run to use (default: the latest serving run)")
    p.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    p.set_defaults(func=_predict_upcoming)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
