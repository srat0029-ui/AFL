"""CLI to tune, evaluate, and persist the logistic-regression match-winner
models — both "stats only" and "stats + Elo" variants.

Usage:
    python -m app.modelling.logistic_cli

Pipeline (mirrors elo_cli.py/poisson_cli.py's structure, adapted for a
static rather than online-updating model — see logistic_tuning.py's module
docstring for why the inner-split discipline differs):
    1. Load completed AFL matches + advanced team stats, and a leakage-safe
       walk-forward Elo probability per match, using Elo's own already-
       persisted, tuned config (never re-derives its own Elo).
    2. Build point-in-time features across full history.
    3. Split into a tune window (< EVALUATION_START_YEAR) and an
       evaluation window (>= EVALUATION_START_YEAR, excluding the current
       still-incomplete season) — the same window Elo/Poisson's own
       evaluation reports use.
    4. For each variant, select regularisation strength C via an inner
       chronological split entirely within the tune window.
    5. Persist each variant's config (feature list, rolling windows, C,
       tune/eval ranges) to ModelRun — for reproducibility/documentation
       only. This never changes what the live dashboard predicts with
       (see app/edges/calculator.py, untouched by this stage).
    6. Print the full comparison: baselines, Elo, Poisson, both logistic
       variants, calibration, bootstrap uncertainty vs. Elo, feature-group
       ablation, and the promotion decision for each variant.
"""

import sys
import warnings
from dataclasses import dataclass

from app.backtesting.evaluation import EVALUATION_START_YEAR
from app.backtesting.logistic_report import INNER_VALIDATION_START_YEAR, build_logistic_comparison
from app.database import SessionLocal
from app.modelling.data_loading import load_completed_matches, load_matches_with_team_stats
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.features import STATS_FEATURE_NAMES, STATS_PLUS_ELO_FEATURE_NAMES, build_match_features
from app.modelling.logistic_tuning import select_best_C
from app.modelling.model_run_persistence import persist_model_run
from app.models import ModelRun
from sqlalchemy import select

warnings.filterwarnings("ignore", category=FutureWarning)  # sklearn's deprecated-but-harmless `penalty` note


@dataclass(frozen=True)
class PersistedLogisticConfig:
    """Everything needed to reproduce a logistic model run — stored in
    ModelRun.config_json alongside Elo/Poisson's own configs, via the same
    persist_model_run() path."""

    feature_set: str  # "stats_only" | "stats_plus_elo"
    feature_names: tuple[str, ...]
    C: float
    random_state: int
    form_window_short: int
    form_window_long: int
    stats_window: int
    tune_start_year: int
    tune_end_year: int
    inner_validation_start_year: int
    evaluation_start_year: int


def _print_variant(label: str, variant, promotion_only: bool = False) -> None:
    print(f"\n{label}: n={variant.n_eval}")
    print(
        f"    brier={variant.brier_score:.4f}  log_loss={variant.log_loss:.4f}  "
        f"accuracy={variant.accuracy:.1%}  ECE={variant.calibration_ece:.4f}  "
        f"calibration={variant.calibration_method}"
    )
    # bootstrap_vs_elo.point_estimate = elo_brier - candidate_brier: positive
    # means the candidate's Brier is lower (better) than Elo's.
    b = variant.bootstrap_vs_elo
    print(
        f"    Brier improvement vs Elo: {b.point_estimate:+.4f}  "
        f"95% bootstrap interval: [{b.ci_low:+.4f}, {b.ci_high:+.4f}]"
    )
    print(f"    Promotion: {'PROMOTE' if variant.promotion.promote else 'KEEP ELO'}")
    for reason in variant.promotion.reasons:
        print(f"      {reason}")


def main(argv: list[str] | None = None) -> int:
    db = SessionLocal()
    try:
        elo_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "elo"))
        poisson_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson"))
        if elo_run is None or poisson_run is None:
            print("Run `python -m app.modelling.elo_cli` and `python -m app.modelling.poisson_cli` first.")
            return 1

        matches = load_matches_with_team_stats(db)
        match_results = load_completed_matches(db)
        if not matches:
            print("No completed matches with advanced stats found — run --team-stats ingestion first.")
            return 1

        elo_config = EloConfig(**elo_run.config_json)
        elo_preds = elo_walk_forward(match_results, elo_config)
        elo_probs_by_match = {p.match_id: p.home_win_probability for p in elo_preds}

        rows = build_match_features(matches, elo_prob_by_match=elo_probs_by_match)
        tune_rows = [r for r in rows if r.season_year < EVALUATION_START_YEAR]
        n_tune_full = sum(1 for r in tune_rows if r.has_full_history)
        print(
            f"Tuning on {n_tune_full} matches with full rolling history "
            f"({min(r.season_year for r in tune_rows)}-{EVALUATION_START_YEAR - 1})..."
        )

        print("\nSelecting regularisation strength (stats only)...")
        C_stats_only, leaderboard_stats = select_best_C(tune_rows, STATS_FEATURE_NAMES, INNER_VALIDATION_START_YEAR)
        print(f"  selected C={C_stats_only}")
        for row in leaderboard_stats[:5]:
            print(f"    C={row['C']:<6}  inner_val_brier={row['inner_val_brier']:.4f}")

        print("\nSelecting regularisation strength (stats + Elo)...")
        C_stats_plus_elo, leaderboard_elo = select_best_C(tune_rows, STATS_PLUS_ELO_FEATURE_NAMES, INNER_VALIDATION_START_YEAR)
        print(f"  selected C={C_stats_plus_elo}")
        for row in leaderboard_elo[:5]:
            print(f"    C={row['C']:<6}  inner_val_brier={row['inner_val_brier']:.4f}")

        print("\nEvaluating both variants on the evaluation-period match set (this runs bootstrap + ablation, ~a few seconds)...")
        overview = build_logistic_comparison(db, C_stats_only=C_stats_only, C_stats_plus_elo=C_stats_plus_elo)

        print(f"\n=== Evaluation period {overview.evaluation_start_year}-{overview.evaluation_end_year}, n={overview.n_eval} ===")
        print("\nBaselines & existing models:")
        for row in [*overview.baselines, overview.elo, overview.poisson]:
            print(f"  {row.name:<32} n={row.n:5d}  brier={row.brier_score:.4f}  log_loss={row.log_loss:.4f}  accuracy={row.accuracy:.1%}")

        _print_variant("Logistic (stats only)", overview.stats_only)
        _print_variant("Logistic (stats + Elo)", overview.stats_plus_elo)

        for variant, C in ((overview.stats_only, C_stats_only), (overview.stats_plus_elo, C_stats_plus_elo)):
            print(f"\n{variant.variant} feature-group ablation (Brier, vs Elo alone):")
            for a in variant.feature_group_ablation:
                delta = f"{a.brier_vs_elo_alone:+.4f}" if a.brier_vs_elo_alone is not None else "n/a"
                print(f"    {a.label:<28} brier={a.brier_score:.4f}  vs_elo={delta}")

            print(f"\n{variant.variant} standardized coefficients (top 5 by magnitude):")
            top_coefs = sorted(variant.standardized_coefficients.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
            for name, coef in top_coefs:
                print(f"    {name:<40} {coef:+.4f}")

            config = PersistedLogisticConfig(
                feature_set=variant.variant,
                feature_names=variant.feature_names,
                C=C,
                random_state=42,
                form_window_short=5,
                form_window_long=10,
                stats_window=6,
                tune_start_year=min(r.season_year for r in tune_rows),
                tune_end_year=EVALUATION_START_YEAR - 1,
                inner_validation_start_year=INNER_VALIDATION_START_YEAR,
                evaluation_start_year=EVALUATION_START_YEAR,
            )
            model_name = "logistic_stats_only" if variant.variant == "stats_only" else "logistic_stats_plus_elo"
            has_edge = variant.brier_score < overview.elo.brier_score
            persist_model_run(
                db,
                model_name=model_name,
                config=config,
                tune_end_year=EVALUATION_START_YEAR - 1,
                metrics=[
                    {
                        "market_type": "h2h",
                        "metric_name": "brier_score",
                        "holdout_n": variant.n_eval,
                        "holdout_value": variant.brier_score,
                        "naive_baseline_value": overview.elo.brier_score,
                        "has_edge_over_naive": has_edge,
                    }
                ],
            )
        print("\nPersisted both variants' configs (for reproducibility — the live dashboard is unaffected).")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
