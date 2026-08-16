"""CLI to run and persist the full disposal-prediction backtest — Section
27 of the disposal-prediction stage brief. Runs baselines A-D and every
candidate model (Ridge, Poisson regression, Negative Binomial regression,
XGBoost, LightGBM) on the real 2016-2025 dataset, evaluates all of them on
the identical eval set, decides promotion honestly (a baseline can win),
and persists every model run for reproducibility.

Usage:
    python -m app.player_modelling.disposal_cli
"""

import sys
import warnings
from dataclasses import asdict

from app.database import SessionLocal
from app.player_modelling.disposal_analysis import (
    by_disposal_level,
    by_history_bucket,
    compare_2020_handling,
    compare_with_without_tog,
    run_ablations,
    select_example_predictions,
)
from app.player_modelling.disposal_backtest import (
    EVALUATION_START_YEAR,
    build_dataset,
    run_baselines,
    run_candidate_models,
)
from app.player_modelling.disposal_data import load_player_game_rows, load_team_game_rows
from app.player_modelling.disposal_evaluation import best_distribution_method, evaluate_model
from app.player_modelling.disposal_features import PLAYER_FEATURE_NAMES, PROMOTED_DISPOSAL_FEATURE_NAMES
from app.player_modelling.disposal_models import BoostingRegressionConfig
from app.player_modelling.disposal_persistence import persist_model_run
from app.player_modelling.disposal_team_context import build_team_context

warnings.filterwarnings("ignore")

MIN_MAE_IMPROVEMENT = 0.02  # a candidate must beat the best baseline's MAE by >=2% to be worth the extra complexity


def main() -> int:
    db = SessionLocal()
    print("Loading data and building point-in-time features (2016-2025)...")
    split = build_dataset(db)
    tune_start_year = min((r.season_year for r in split.tune_rows), default=EVALUATION_START_YEAR)
    tune_end_year = EVALUATION_START_YEAR - 1
    print(f"  eligible rows: {len(split.all_rows)}  tune: {len(split.tune_rows)}  eval: {len(split.eval_rows)}")

    print("\nRunning baselines A-D...")
    baseline_predictions = run_baselines(split)

    print("Fitting candidate models (ridge, poisson, negative_binomial, gbm_xgboost, gbm_lightgbm)...")
    model_predictions = run_candidate_models(split)
    feature_names_by_model = {name: PLAYER_FEATURE_NAMES for name in model_predictions}

    # Resolve a real ambiguity found in the first disposal report: the
    # all-features Ridge (31 features) reported MAE 3.960, but the
    # player-history+opponent-context ablation reported MAE 3.954 on what
    # should be the same eval rows. Refit that exact reduced configuration
    # here and compare it against all-features Ridge on every metric the
    # report cares about (not just MAE) - only swap it in if it's genuinely
    # equal-or-better, never merely because it's simpler OR merely because
    # it has more features.
    print("\nResolving all-features vs history+opponent-context Ridge ambiguity...")
    ridge_reduced_preds = run_candidate_models(split, feature_names=PROMOTED_DISPOSAL_FEATURE_NAMES, model_names=("ridge",))["ridge"]
    eval_all = evaluate_model("ridge_all_features", model_predictions["ridge"], "nb")
    eval_reduced = evaluate_model("ridge_history_opponent", ridge_reduced_preds, "nb")
    print(
        f"  all_features   ({len(PLAYER_FEATURE_NAMES)} feat): mae={eval_all.point.mae:.4f} rmse={eval_all.point.rmse:.4f} "
        f"bias={eval_all.point.bias:+.4f}"
    )
    print(
        f"  history+opp    ({len(PROMOTED_DISPOSAL_FEATURE_NAMES)} feat): mae={eval_reduced.point.mae:.4f} "
        f"rmse={eval_reduced.point.rmse:.4f} bias={eval_reduced.point.bias:+.4f}"
    )
    for t in (20, 25, 30, 35):
        a, r = eval_all.thresholds[t], eval_reduced.thresholds[t]
        print(f"    {t}+ brier: all={a.brier:.4f} reduced={r.brier:.4f} | ece: all={a.ece:.4f} reduced={r.ece:.4f}")
    for c in (0.5, 0.8, 0.9):
        a, r = eval_all.intervals[c], eval_reduced.intervals[c]
        print(f"    {int(c*100)}% interval coverage: all={a.empirical_coverage:.3f} reduced={r.empirical_coverage:.3f}")

    reduced_wins = (
        eval_reduced.point.mae <= eval_all.point.mae
        and eval_reduced.point.rmse <= eval_all.point.rmse
        and abs(eval_reduced.point.bias) <= abs(eval_all.point.bias) + 0.01  # allow negligible noise
    )
    if reduced_wins:
        print("  -> history+opponent-context Ridge is genuinely equal-or-better - using it as the promoted configuration.")
        model_predictions["ridge"] = ridge_reduced_preds
        feature_names_by_model["ridge"] = PROMOTED_DISPOSAL_FEATURE_NAMES
    else:
        print("  -> all-features Ridge remains better - keeping it as the promoted configuration.")

    all_predictions = {**baseline_predictions, **model_predictions}
    configs = {
        "baseline_last5": {},
        "baseline_last10": {},
        "baseline_ewma": {},
        "baseline_season_avg": {},
        "ridge": {"alpha": 5.0},
        "poisson_regression": {},
        "negative_binomial": {},
        "gbm_xgboost": asdict(BoostingRegressionConfig(library="xgboost")),
        "gbm_lightgbm": asdict(BoostingRegressionConfig(library="lightgbm")),
    }

    print("\n=== Point-prediction MAE (common eval set, n={}) ===".format(len(split.eval_rows)))
    mae_by_name = {}
    for name, preds in all_predictions.items():
        pm = evaluate_model(name, preds, "nb").point
        mae_by_name[name] = pm.mae
        print(f"  {name:24s} mae={pm.mae:.4f} rmse={pm.rmse:.4f} bias={pm.bias:+.4f}")

    best_baseline_mae = min(mae_by_name[n] for n in baseline_predictions)
    best_name = min(mae_by_name, key=lambda n: mae_by_name[n])
    best_mae = mae_by_name[best_name]

    is_baseline_best = best_name in baseline_predictions
    promoted_name = best_name
    if not is_baseline_best and (best_baseline_mae - best_mae) / best_baseline_mae < MIN_MAE_IMPROVEMENT:
        # The apparent winner is a fitted model, but its improvement over
        # the best baseline is too small to justify extra complexity - the
        # brief explicitly asks not to force promotion of a fancier model.
        second_best_baseline = min(baseline_predictions, key=lambda n: mae_by_name[n])
        print(
            f"\n{best_name} narrowly beats the best baseline "
            f"({(best_baseline_mae - best_mae) / best_baseline_mae:.1%} improvement, "
            f"below the {MIN_MAE_IMPROVEMENT:.0%} threshold) - promoting the simpler baseline instead."
        )
        promoted_name = second_best_baseline

    print(f"\nBest by MAE: {best_name} ({best_mae:.4f}). Promoted model: {promoted_name}")

    print("\nDetermining distribution method per model (NB vs empirical-residual)...")
    distribution_methods = {name: best_distribution_method(preds) for name, preds in all_predictions.items()}
    for name, method in distribution_methods.items():
        print(f"  {name}: {method}")

    print("\nPersisting all model runs...")
    for name, preds in all_predictions.items():
        method = distribution_methods[name]
        evaluation = evaluate_model(name, preds, method)
        persist_model_run(
            db,
            model_name=f"disposals_{name}",
            feature_names=feature_names_by_model.get(name, ()),
            config=configs.get(name, {}),
            distribution_method=method,
            tune_start_year=tune_start_year,
            tune_end_year=tune_end_year,
            evaluation=evaluation,
            predictions=preds,
            is_promoted=(name == promoted_name),
        )
    print("  done.")

    print("\n=== Threshold calibration (promoted model, 20+/25+/30+/35+) ===")
    promoted_preds = all_predictions[promoted_name]
    promoted_method = distribution_methods[promoted_name]
    promoted_eval = evaluate_model(promoted_name, promoted_preds, promoted_method)
    for t, tm in promoted_eval.thresholds.items():
        print(f"  {t:g}+: brier={tm.brier:.4f} log_loss={tm.log_loss:.4f} ece={tm.ece}")

    print("\n=== Prediction interval coverage (promoted model) ===")
    for c, im in promoted_eval.intervals.items():
        print(f"  {int(c*100)}%: empirical_coverage={im.empirical_coverage:.3f} mean_width={im.mean_width:.2f}")

    print("\n=== Season stability (promoted model) ===")
    for year, pm in sorted(promoted_eval.point_by_season.items()):
        print(f"  {year}: n={pm.n} mae={pm.mae:.4f} rmse={pm.rmse:.4f} bias={pm.bias:+.4f}")

    print("\n=== Player-history buckets (promoted model) ===")
    for bucket, stats in by_history_bucket(promoted_preds).items():
        print(f"  {bucket}: {stats}")

    print("\n=== Disposal-level buckets (promoted model) ===")
    for bucket, stats in by_disposal_level(promoted_preds).items():
        print(f"  {bucket}: {stats}")

    print("\n=== TOG comparison (ridge vehicle) ===")
    tog_result = compare_with_without_tog(split)
    print(f"  {tog_result}")

    print("\n=== Feature-group ablations (ridge vehicle) ===")
    for combo, stats in run_ablations(split).items():
        print(f"  {combo}: {stats}")

    print("\n=== 2020 shortened-quarter handling ===")
    player_rows = load_player_game_rows(db)
    team_rows = load_team_game_rows(db)
    team_context = build_team_context(db)
    result_2020 = compare_2020_handling(player_rows, team_rows, team_context)
    print(f"  {result_2020}")

    print("\n=== Example predictions (promoted model) ===")
    for ex in select_example_predictions(promoted_preds, promoted_method):
        print(
            f"  [{ex.category}] player={ex.player_id} match={ex.match_id} season={ex.season_year} "
            f"history={ex.games_of_history} predicted={ex.predicted_mean:.1f} actual={ex.actual} "
            f"50%={ex.interval_50} 80%={ex.interval_80} "
            f"P20+={ex.prob_20_plus:.2f} P25+={ex.prob_25_plus:.2f} P30+={ex.prob_30_plus:.2f} P35+={ex.prob_35_plus:.2f}"
        )

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
