"""CLI to tune, validate, and persist the AFL Elo match-winner model.

Usage:
    python -m app.modelling.elo_cli
    python -m app.modelling.elo_cli --tune-end-year 2021

Pipeline:
    1. Load completed AFL matches from the DB.
    2. Select Elo hyperparameters via grid search, scored only on matches up
       to --tune-end-year (default 2022).
    3. Re-run the full walk-forward replay (all seasons) with that config.
    4. Report Brier score / log loss / accuracy / calibration separately for
       the tune window (in-sample for selection) and the holdout window
       (never touched during selection) — the holdout numbers are the ones
       that actually mean something.
    5. Persist the full rating history to the elo_ratings table.
    6. Print a sanity-check preview of model probabilities for upcoming
       (not-yet-played) fixtures.
"""

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.modelling.data_loading import load_completed_matches
from app.modelling.elo import EloEngine
from app.modelling.elo_backtest import current_ratings, run_walk_forward
from app.modelling.elo_persistence import persist_elo_ratings
from app.modelling.elo_tuning import select_best_config
from app.modelling.metrics import accuracy, brier_score, calibration_table, log_loss
from app.models import Match, MatchStatus, Sport

DEFAULT_TUNE_END_YEAR = 2022


def _print_metrics(label: str, predictions) -> None:
    if not predictions:
        print(f"{label}: no matches")
        return
    probs = [p.home_win_probability for p in predictions]
    outcomes = [p.actual_home_outcome for p in predictions]
    print(
        f"{label}: n={len(predictions)}  "
        f"brier={brier_score(probs, outcomes):.4f}  "
        f"log_loss={log_loss(probs, outcomes):.4f}  "
        f"accuracy={accuracy(probs, outcomes):.1%}"
    )


def print_upcoming_predictions(db: Session, engine: EloEngine, ratings: dict[int, tuple[float, int]]) -> None:
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
        season_year = match.season.year
        home_rating = _rating_for_upcoming(ratings, engine, match.home_team_id, season_year)
        away_rating = _rating_for_upcoming(ratings, engine, match.away_team_id, season_year)
        prob = engine.expected_home_win_prob(home_rating, away_rating)
        print(
            f"  {match.home_team.name:22s} {prob:5.1%}  vs  "
            f"{match.away_team.name:22s} {1 - prob:5.1%}   ({match.scheduled_start:%Y-%m-%d})"
        )


def _rating_for_upcoming(
    ratings: dict[int, tuple[float, int]], engine: EloEngine, team_id: int, season_year: int
) -> float:
    rating, last_season = ratings.get(team_id, (engine.config.initial_rating, season_year))
    if last_season != season_year:
        rating = engine.regress_to_mean(rating)
    return rating


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tune, validate, and persist the AFL Elo model.")
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
            print("No completed matches found — run data ingestion first (Stage 1.1).")
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
        print("\nTop 5 configs by tune-window Brier score:")
        for row in leaderboard[:5]:
            print(f"  brier={row['tune_brier']:.4f}  {row['config']}")

        final_predictions = run_walk_forward(matches, best_config)
        tune_preds = [p for p in final_predictions if p.season_year <= args.tune_end_year]
        holdout_preds = [p for p in final_predictions if p.season_year > args.tune_end_year]

        print()
        _print_metrics(f"Tune window ({matches[0].season_year}-{args.tune_end_year}, in-sample for selection)", tune_preds)
        _print_metrics(
            f"Holdout window ({holdout_years_present[0]}-{holdout_years_present[-1]}, never used for selection)",
            holdout_preds,
        )

        print("\nCalibration on holdout window (predicted probability bucket vs actual home-win rate):")
        for row in calibration_table(
            [p.home_win_probability for p in holdout_preds], [p.actual_home_outcome for p in holdout_preds]
        ):
            if row["n"] == 0:
                continue
            print(f"  {row['bucket']}: n={row['n']:4d}  predicted={row['avg_predicted']:.2f}  actual={row['actual_rate']:.2f}")

        written = persist_elo_ratings(db, final_predictions)
        print(f"\nPersisted {written} team-match rating snapshots.")

        engine = EloEngine(best_config)
        ratings = current_ratings(final_predictions)
        print_upcoming_predictions(db, engine, ratings)

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
