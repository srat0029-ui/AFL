"""CLI to tune, validate, and persist the AFL Poisson scoring model.

Usage:
    python -m app.modelling.poisson_cli
    python -m app.modelling.poisson_cli --tune-end-year 2021

Pipeline:
    1. Load completed AFL matches from the DB.
    2. Select rolling-window / shrinkage hyperparameters via grid search,
       scored on total-points MAE, only on matches up to --tune-end-year
       (default 2022).
    3. Re-run the full walk-forward replay (all seasons) with that config.
    4. Report total-points MAE/RMSE, margin MAE/RMSE, and win-probability
       Brier/log-loss/accuracy/calibration — separately for the tune window
       (in-sample for selection) and the holdout window (never touched
       during selection).
    5. Persist the full prediction history to poisson_match_predictions.
    6. Print a sanity-check preview of expected scorelines for upcoming
       (not-yet-played) fixtures.
"""

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.modelling.data_loading import load_completed_matches
from app.modelling.metrics import accuracy, brier_score, calibration_table, log_loss, mae, rmse
from app.modelling.poisson_backtest import PoissonModelState, run_walk_forward
from app.modelling.poisson_model import expected_value, win_draw_loss
from app.modelling.poisson_persistence import persist_poisson_predictions
from app.modelling.poisson_tuning import select_best_config
from app.models import Match, MatchStatus, Sport

DEFAULT_TUNE_END_YEAR = 2022


def _print_metrics(label: str, predictions, naive_total: float, naive_margin: float) -> None:
    """naive_total/naive_margin: a "just predict the tune-window average
    every time" baseline, computed once from the tune window and reused for
    both windows here. If the model's own MAE isn't clearly better than
    this trivial constant, that's flagged rather than hidden — a model
    barely beating "always guess the average" has no real edge in that
    market, however plausible its raw MAE number looks in isolation.
    """
    if not predictions:
        print(f"{label}: no matches")
        return
    total_pred = [p.expected_total_points for p in predictions]
    total_actual = [p.actual_total_points for p in predictions]
    margin_pred = [p.expected_margin for p in predictions]
    margin_actual = [p.actual_margin for p in predictions]
    win_probs = [p.home_win_probability for p in predictions]
    outcomes = [p.actual_home_outcome for p in predictions]

    naive_total_mae = mae([naive_total] * len(predictions), total_actual)
    naive_margin_mae = mae([naive_margin] * len(predictions), margin_actual)
    model_total_mae = mae(total_pred, total_actual)
    model_margin_mae = mae(margin_pred, margin_actual)

    def _edge_flag(model_mae: float, naive_mae: float) -> str:
        improvement = (naive_mae - model_mae) / naive_mae
        if improvement > 0.05:
            return f"beats naive by {improvement:.0%}"
        return "NO CLEAR EDGE over naive 'always predict the average'"

    print(
        f"{label}: n={len(predictions)}\n"
        f"    total points   mae={model_total_mae:6.2f}  rmse={rmse(total_pred, total_actual):6.2f}"
        f"  (naive mae={naive_total_mae:.2f} - {_edge_flag(model_total_mae, naive_total_mae)})\n"
        f"    margin         mae={model_margin_mae:6.2f}  rmse={rmse(margin_pred, margin_actual):6.2f}"
        f"  (naive mae={naive_margin_mae:.2f} - {_edge_flag(model_margin_mae, naive_margin_mae)})\n"
        f"    win probability brier={brier_score(win_probs, outcomes):.4f}  "
        f"log_loss={log_loss(win_probs, outcomes):.4f}  accuracy={accuracy(win_probs, outcomes):.1%}"
    )


def print_upcoming_predictions(db: Session, state: PoissonModelState) -> None:
    upcoming = db.scalars(
        select(Match)
        .join(Sport)
        .where(Sport.code == "AFL", Match.status == MatchStatus.SCHEDULED)
        .order_by(Match.scheduled_start)
    ).all()
    if not upcoming:
        print("\nNo upcoming fixtures to preview.")
        return

    print(f"\nUpcoming fixture predictions ({len(upcoming)}):")
    for match in upcoming:
        home_pmf, away_pmf = state.predict(match.home_team_id, match.away_team_id)
        home_expected = expected_value(home_pmf)
        away_expected = expected_value(away_pmf)
        home_win, draw, away_win = win_draw_loss(home_pmf, away_pmf)
        print(
            f"  {match.home_team.name:22s} {home_expected:5.1f}  vs  "
            f"{match.away_team.name:22s} {away_expected:5.1f}   "
            f"(win {home_win:.0%}/{draw:.0%}/{away_win:.0%})   ({match.scheduled_start:%Y-%m-%d})"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tune, validate, and persist the AFL Poisson scoring model.")
    parser.add_argument(
        "--tune-end-year",
        type=int,
        default=DEFAULT_TUNE_END_YEAR,
        help=f"Last season year used for hyperparameter selection (default {DEFAULT_TUNE_END_YEAR}); "
        "later seasons are held out and used only for reporting.",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        matches = load_completed_matches(db)
        if not matches:
            print("No completed matches found - run data ingestion first (Stage 1.1).")
            return 1
        if not any(m.home_goals is not None for m in matches):
            print("No goals/behinds data found - re-run data ingestion to backfill it (see Stage 1.3 notes).")
            return 1

        tune_matches = [m for m in matches if m.season_year <= args.tune_end_year]
        holdout_years_present = sorted({m.season_year for m in matches if m.season_year > args.tune_end_year})
        if not tune_matches or not holdout_years_present:
            print(
                f"--tune-end-year {args.tune_end_year} leaves nothing on one side of the split "
                f"(data spans {matches[0].season_year}-{matches[-1].season_year})."
            )
            return 1

        print(f"Tuning on {len(tune_matches)} matches ({matches[0].season_year}-{args.tune_end_year})...")
        best_config, leaderboard = select_best_config(tune_matches)

        print(f"\nSelected config: {best_config}")
        print("\nTop 5 configs by tune-window total-points MAE:")
        for row in leaderboard[:5]:
            print(f"  mae={row['tune_total_points_mae']:.2f}  {row['config']}")

        final_predictions, state = run_walk_forward(matches, best_config, return_state=True)
        tune_preds = [p for p in final_predictions if p.season_year <= args.tune_end_year]
        holdout_preds = [p for p in final_predictions if p.season_year > args.tune_end_year]

        # naive baseline: "always predict the tune window's average" — computed
        # once from the tune window only, reused for both windows below, so a
        # market where the model has no real edge over a trivial constant gets
        # flagged rather than just reporting a plausible-looking MAE in isolation.
        naive_total = sum(p.actual_total_points for p in tune_preds) / len(tune_preds)
        naive_margin = sum(p.actual_margin for p in tune_preds) / len(tune_preds)

        print()
        _print_metrics(
            f"Tune window ({matches[0].season_year}-{args.tune_end_year}, in-sample for selection)",
            tune_preds, naive_total, naive_margin,
        )
        print()
        _print_metrics(
            f"Holdout window ({holdout_years_present[0]}-{holdout_years_present[-1]}, never used for selection)",
            holdout_preds, naive_total, naive_margin,
        )

        print("\nWin-probability calibration on holdout window (predicted bucket vs actual home-win rate):")
        for row in calibration_table(
            [p.home_win_probability for p in holdout_preds], [p.actual_home_outcome for p in holdout_preds]
        ):
            if row["n"] == 0:
                continue
            print(f"  {row['bucket']}: n={row['n']:4d}  predicted={row['avg_predicted']:.2f}  actual={row['actual_rate']:.2f}")

        written = persist_poisson_predictions(db, final_predictions)
        print(f"\nPersisted {written} match predictions.")

        print_upcoming_predictions(db, state)

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
