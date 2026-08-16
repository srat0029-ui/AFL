"""CLI to run and persist the full goal-prediction backtest — Section 28.
Runs baselines A-E and every candidate model (Poisson, Negative Binomial,
Hurdle, XGBoost, LightGBM) on the real 2016-2025 dataset, evaluates all of
them on the identical eval set, decides promotion honestly against the
brief's Section 26 criteria (a baseline can win; the most complex model
does not automatically win), and persists every model run for
reproducibility.

Usage:
    python -m app.player_modelling.goal_cli
"""

import sys
import warnings
from dataclasses import asdict

from app.database import SessionLocal
from app.player_modelling.disposal_team_context import build_team_context
from app.player_modelling.goal_analysis import (
    by_history_bucket,
    by_scoring_archetype,
    compare_2020_handling,
    compare_hurdle_vs_nb,
    compare_with_without_tog,
    evaluate_ranking_quality,
    measure_team_goal_consistency,
    run_goal_ablations,
    select_goal_example_predictions,
)
from app.player_modelling.goal_backtest import (
    EVALUATION_START_YEAR,
    build_goal_dataset,
    run_goal_baselines,
    run_goal_candidate_models,
)
from app.player_modelling.goal_data import load_player_goal_game_rows, load_team_goal_game_rows
from app.player_modelling.goal_evaluation import evaluate_goal_model
from app.player_modelling.goal_features import PLAYER_FEATURE_NAMES
from app.player_modelling.goal_models import BoostingGoalConfig
from app.player_modelling.goal_persistence import persist_goal_model_run

warnings.filterwarnings("ignore")


def main() -> int:
    db = SessionLocal()
    print("Loading data and building point-in-time goal features (2016-2025)...")
    split = build_goal_dataset(db)
    tune_start_year = min((r.season_year for r in split.tune_rows), default=EVALUATION_START_YEAR)
    tune_end_year = EVALUATION_START_YEAR - 1
    print(f"  eligible rows: {len(split.all_rows)}  tune: {len(split.tune_rows)}  eval: {len(split.eval_rows)}")

    print("\nRunning baselines A-E...")
    baseline_predictions = run_goal_baselines(split)

    print("Fitting candidate models (poisson, negative_binomial, hurdle, gbm_xgboost, gbm_lightgbm)...")
    model_predictions = run_goal_candidate_models(split)

    all_predictions = {**baseline_predictions, **model_predictions}
    distribution_kinds = {name: ("hurdle" if name == "hurdle" else "nb") for name in all_predictions}
    configs = {
        "baseline_last5": {}, "baseline_last10": {}, "baseline_ewma": {}, "baseline_season_avg": {}, "baseline_team_adjusted_rate": {},
        "poisson_regression": {}, "negative_binomial": {}, "hurdle": {},
        "gbm_xgboost": asdict(BoostingGoalConfig(library="xgboost")), "gbm_lightgbm": asdict(BoostingGoalConfig(library="lightgbm")),
    }
    feature_names_by_model = {name: PLAYER_FEATURE_NAMES for name in model_predictions}

    print(f"\n=== Point-prediction MAE (common eval set, n={len(split.eval_rows)}) ===")
    mae_by_name = {}
    for name, preds in all_predictions.items():
        pm = evaluate_goal_model(name, preds).point
        mae_by_name[name] = pm.mae
        print(f"  {name:28s} mae={pm.mae:.4f} rmse={pm.rmse:.4f} bias={pm.bias:+.4f}")

    print("\n=== 1+ goal probability calibration (every model, common eval set) ===")
    ece_1plus_by_name = {}
    for name, preds in all_predictions.items():
        ev = evaluate_goal_model(name, preds)
        t1 = ev.thresholds[1]
        ece_1plus_by_name[name] = t1.ece if t1.ece is not None else 999.0
        print(f"  {name:28s} brier={t1.brier:.4f} ece={t1.ece}")

    # Promotion (Section 26, pre-specified before looking at which model
    # would win, to avoid picking the rule to fit a result): the brief is
    # explicit that point MAE must not be the sole selection metric for a
    # low-count, zero-heavy target, and that probability calibration
    # matters more. Rule: among every model (baseline or fitted) whose MAE
    # is within MAE_TOLERANCE of the single best MAE anywhere, promote
    # whichever has the best 1+ calibration (lowest ECE). This lets a
    # baseline win outright if its MAE is essentially tied for best AND its
    # calibration is also best; it also lets a fitted model with slightly
    # worse MAE win if its calibration is meaningfully better - exactly the
    # trade-off Section 11/26 ask for.
    MAE_TOLERANCE = 0.10  # 10% - a model within this of the best MAE is "MAE-competitive," calibration then decides
    overall_best_mae = min(mae_by_name.values())
    mae_competitive = [n for n in mae_by_name if mae_by_name[n] <= overall_best_mae * (1 + MAE_TOLERANCE)]
    promoted_name = min(mae_competitive, key=lambda n: ece_1plus_by_name[n])

    print(f"\nMAE-competitive models (within {MAE_TOLERANCE:.0%} of best MAE {overall_best_mae:.4f}): {mae_competitive}")
    print(f"Promoted (best 1+ calibration among MAE-competitive models): {promoted_name} (mae={mae_by_name[promoted_name]:.4f}, 1+ ece={ece_1plus_by_name[promoted_name]:.4f})")

    print("\n=== Hurdle vs plain NB - is the extra zero-handling worth it? ===")
    if "hurdle" in model_predictions and "negative_binomial" in model_predictions:
        cmp = compare_hurdle_vs_nb(model_predictions["negative_binomial"], model_predictions["hurdle"])
        print(f"  {cmp}")

    print(f"\nPromoted model: {promoted_name}")

    print("\nPersisting all model runs...")
    for name, preds in all_predictions.items():
        evaluation = evaluate_goal_model(name, preds)
        persist_goal_model_run(
            db,
            model_name=f"goals_{name}",
            feature_names=feature_names_by_model.get(name, ()),
            config=configs.get(name, {}),
            distribution_kind=distribution_kinds[name],
            tune_start_year=tune_start_year,
            tune_end_year=tune_end_year,
            evaluation=evaluation,
            predictions=preds,
            is_promoted=(name == promoted_name),
        )
    print("  done.")

    promoted_preds = all_predictions[promoted_name]
    promoted_eval = evaluate_goal_model(promoted_name, promoted_preds)

    print("\n=== Threshold calibration (promoted model, 1+/2+/3+/4+/5+) ===")
    for t, tm in promoted_eval.thresholds.items():
        print(f"  {t:g}+: n_positive={tm.n_positive} brier={tm.brier:.4f} log_loss={tm.log_loss:.4f} ece={tm.ece} low_sample_warning={tm.low_sample_warning}")

    print("\n=== Zero-goal calibration (promoted model) ===")
    zg = promoted_eval.zero_goal
    print(f"  brier={zg.brier:.4f} ece={zg.ece} mean_predicted_p0={zg.mean_predicted_p0:.3f} actual_p0={zg.actual_p0:.3f}")

    print("\n=== Season stability (promoted model) ===")
    for year, pm in sorted(promoted_eval.point_by_season.items()):
        print(f"  {year}: n={pm.n} mae={pm.mae:.4f} bias={pm.bias:+.4f}")

    print("\n=== Player-history buckets (promoted model) ===")
    for bucket, stats in by_history_bucket(promoted_preds).items():
        print(f"  {bucket}: {stats}")

    print("\n=== Scoring archetypes (promoted model) ===")
    for bucket, stats in by_scoring_archetype(promoted_preds, split.eval_rows).items():
        print(f"  {bucket}: {stats}")

    print("\n=== Feature ablations (NB vehicle) ===")
    for combo, stats in run_goal_ablations(split).items():
        print(f"  {combo}: {stats}")

    print("\n=== TOG comparison (NB vehicle) ===")
    print(f"  {compare_with_without_tog(split)}")

    print("\n=== Team-goal consistency (promoted model) ===")
    team_context = build_team_context(db)
    print(f"  {measure_team_goal_consistency(promoted_preds, split.eval_rows, team_context)}")

    print("\n=== Ranking quality (promoted model) ===")
    print(f"  {evaluate_ranking_quality(promoted_preds)}")

    print("\n=== 2020 shortened-quarter handling ===")
    player_rows = load_player_goal_game_rows(db)
    team_rows = load_team_goal_game_rows(db)
    print(f"  {compare_2020_handling(player_rows, team_rows, team_context)}")

    print("\n=== Example predictions (promoted model) ===")
    for ex in select_goal_example_predictions(promoted_preds):
        print(
            f"  [{ex.category}] player={ex.player_id} match={ex.match_id} season={ex.season_year} history={ex.games_of_history} "
            f"predicted={ex.predicted_mean:.2f} actual={ex.actual} "
            f"P1+={ex.prob_1_plus:.2f} P2+={ex.prob_2_plus:.2f} P3+={ex.prob_3_plus:.2f} P4+={ex.prob_4_plus:.2f}"
        )

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
