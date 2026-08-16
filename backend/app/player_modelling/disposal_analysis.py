"""Deeper diagnostics beyond headline MAE/calibration - Sections 15-19 of
the disposal-prediction stage brief: where the model can/can't be trusted
(player-history buckets, disposal-level buckets), whether TOG history
actually helps, which feature groups matter, and how to handle 2020's
shortened quarters without letting them distort later baselines.
"""

from dataclasses import dataclass

import numpy as np

from app.player_modelling.disposal_backtest import DatasetSplit, PredictionRecord, build_dataset_from_rows, run_candidate_models
from app.player_modelling.disposal_evaluation import compute_point_metrics, compute_threshold_metrics
from app.player_modelling.disposal_features import PLAYER_FEATURE_NAMES

HISTORY_BUCKETS = (("<10", 0, 10), ("10-30", 10, 30), ("30-75", 30, 75), ("75+", 75, 10_000))
DISPOSAL_LEVEL_BUCKETS = (("low (<12)", 0, 12), ("medium (12-22)", 12, 22), ("high (22+)", 22, 10_000))


def by_history_bucket(predictions: list[PredictionRecord]) -> dict[str, dict]:
    rows = []
    for label, lo, hi in HISTORY_BUCKETS:
        bucket = [p for p in predictions if lo <= p.games_of_history < hi]
        if not bucket:
            rows.append((label, None))
            continue
        pm = compute_point_metrics(bucket)
        t20 = compute_threshold_metrics(bucket, 20, "nb")
        rows.append((label, {"n": pm.n, "mae": pm.mae, "rmse": pm.rmse, "bias": pm.bias, "brier_20": t20.brier}))
    return dict(rows)


def by_disposal_level(predictions: list[PredictionRecord]) -> dict[str, dict]:
    rows = []
    for label, lo, hi in DISPOSAL_LEVEL_BUCKETS:
        bucket = [p for p in predictions if lo <= p.actual < hi]
        if not bucket:
            rows.append((label, None))
            continue
        pm = compute_point_metrics(bucket)
        rows.append((label, {"n": pm.n, "mae": pm.mae, "rmse": pm.rmse, "bias": pm.bias}))
    return dict(rows)


@dataclass(frozen=True)
class TOGComparison:
    with_tog_mae: float
    without_tog_mae: float
    with_tog_n: int
    without_tog_n: int
    high_variance_tog_mae: float | None
    stable_tog_mae: float | None


TOG_FEATURE_NAMES = ("tog_last3_avg", "tog_last5_avg", "tog_last10_avg", "disposals_per_tog100_career", "tog_trend")


def compare_with_without_tog(split: DatasetSplit) -> TOGComparison:
    """Fits the same model (Ridge - the fastest, and per the real backtest
    run also the current MAE leader) once with the full feature set and
    once with TOG features removed, both on the identical tune/eval rows,
    to isolate what TOG history actually contributes. Never uses the
    target match's own TOG in either case - both feature sets are already
    built exclusively from PRIOR matches (see disposal_features.py); this
    ablation is about whether HISTORICAL TOG helps, not about leaking the
    target match's TOG (which the feature builder structurally cannot do)."""
    full_features = PLAYER_FEATURE_NAMES
    no_tog_features = tuple(f for f in PLAYER_FEATURE_NAMES if f not in TOG_FEATURE_NAMES)

    with_tog = run_candidate_models(split, feature_names=full_features, model_names=("ridge",))["ridge"]
    without_tog = run_candidate_models(split, feature_names=no_tog_features, model_names=("ridge",))["ridge"]

    with_pm = compute_point_metrics(with_tog)
    without_pm = compute_point_metrics(without_tog)

    # Split the WITH-TOG predictions by recent TOG level as a proxy for
    # sub/rotation risk (PredictionRecord only carries tog_last5_avg, not a
    # variance feature, so level - not swing - is what's available to split
    # on here): the bottom quartile of recent TOG is the strongest
    # observable proxy this dataset has for role uncertainty.
    tog_values = [p.tog_last5_avg for p in with_tog if p.tog_last5_avg is not None]
    if tog_values:
        low_cut = float(np.percentile(tog_values, 25))
        low_tog = [p for p in with_tog if p.tog_last5_avg is not None and p.tog_last5_avg <= low_cut]
        stable_tog = [p for p in with_tog if p.tog_last5_avg is not None and p.tog_last5_avg > low_cut]
        high_var_mae = compute_point_metrics(low_tog).mae if low_tog else None
        stable_mae = compute_point_metrics(stable_tog).mae if stable_tog else None
    else:
        high_var_mae = stable_mae = None

    return TOGComparison(
        with_tog_mae=with_pm.mae,
        without_tog_mae=without_pm.mae,
        with_tog_n=with_pm.n,
        without_tog_n=without_pm.n,
        high_variance_tog_mae=high_var_mae,
        stable_tog_mae=stable_mae,
    )


FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "player_history": (
        "disposals_last3_avg", "disposals_last5_avg", "disposals_last10_avg", "disposals_season_avg",
        "disposals_career_avg", "disposals_last5_median", "disposals_last5_std", "disposals_last10_std",
        "disposals_ewma", "kicks_last5_avg", "handballs_last5_avg", "marks_last5_avg", "tackles_last5_avg",
        "clearances_last5_avg", "inside_50s_last5_avg", "contested_possessions_last5_avg",
        "uncontested_possessions_last5_avg", "disposal_trend",
    ),
    "tog": TOG_FEATURE_NAMES,
    "team_context": ("team_recent_disposals_avg", "team_elo_win_prob", "team_expected_score", "expected_margin", "is_home"),
    "opponent_context": ("opponent_disposals_conceded_avg", "opponent_expected_score"),
    "venue": ("venue_disposals_env",),
}


def run_ablations(split: DatasetSplit) -> dict[str, dict]:
    """Section 17: player-history-only, +team, +opponent, +TOG, +match
    context (Elo/Poisson), and all-selected-features - using Ridge (fast,
    and the current real-data MAE leader) as the ablation vehicle so this
    stays cheap enough to run every combination rather than just one."""
    base = FEATURE_GROUPS["player_history"]
    combos = {
        "player_history_only": base,
        "player_history_plus_team": base + FEATURE_GROUPS["team_context"],
        "player_history_plus_opponent": base + FEATURE_GROUPS["opponent_context"],
        "player_history_plus_tog": base + FEATURE_GROUPS["tog"],
        "player_history_plus_match_context": base + FEATURE_GROUPS["team_context"] + FEATURE_GROUPS["opponent_context"],
        "all_features": PLAYER_FEATURE_NAMES,
    }
    results = {}
    for combo_name, feature_names in combos.items():
        preds = run_candidate_models(split, feature_names=feature_names, model_names=("ridge",))["ridge"]
        pm = compute_point_metrics(preds)
        t20 = compute_threshold_metrics(preds, 20, "nb")
        results[combo_name] = {"n_features": len(feature_names), "mae": pm.mae, "rmse": pm.rmse, "ece_20": t20.ece}
    return results


SHORTENED_SEASON_YEAR = 2020


def compare_2020_handling(player_rows, team_rows, team_context: dict[int, dict[int, dict]]) -> dict[str, dict]:
    """Section 19: empirically compares leaving 2020 unadjusted against
    rescaling how 2020 games feed into rolling HISTORY (see
    DisposalFeatureBuilder's season_scale_factors) - never touching what a
    2020 row itself is scored against. The scale factor is derived
    directly from league-wide averages (see the disposal audit:
    2020 avg=13.44 disposals/game vs 16.83 in 2019 and 15.88 in 2021), not
    guessed. Reports overall 2019-2025 eval MAE for both variants AND,
    separately, MAE restricted to early-2021 rows specifically (rounds 1-6)
    - the exact window where 2020 history contaminating a rolling average
    would show up most, since by mid-2021 most players' rolling windows
    have mostly/entirely rolled past their 2020 games."""
    unadjusted = build_dataset_from_rows(player_rows, team_rows, team_context, season_scale_factors=None)

    adjacent_avg = (16.83 + 15.88) / 2  # 2019 and 2021 league-wide disposal averages, from the disposal audit
    scale = adjacent_avg / 13.44  # 2020's own league-wide average
    adjusted = build_dataset_from_rows(
        player_rows, team_rows, team_context, season_scale_factors={SHORTENED_SEASON_YEAR: scale}
    )

    def _score(split: DatasetSplit) -> dict:
        preds = run_candidate_models(split, model_names=("ridge",))["ridge"]
        overall = compute_point_metrics(preds)
        round_by_key = {(r.match_id, r.player_id): r.round_number for r in split.eval_rows}
        early_2021 = [
            p for p in preds if p.season_year == 2021 and round_by_key.get((p.match_id, p.player_id), 999) <= 6
        ]
        early_metrics = compute_point_metrics(early_2021) if early_2021 else None
        return {
            "overall_mae": overall.mae,
            "overall_rmse": overall.rmse,
            "early_2021_n": len(early_2021),
            "early_2021_mae": early_metrics.mae if early_metrics else None,
        }

    return {
        "scale_factor_used": scale,
        "unadjusted": _score(unadjusted),
        "scaled_2020_history": _score(adjusted),
    }


@dataclass(frozen=True)
class ExamplePrediction:
    """One real historical prediction, formatted for the "individual
    example predictions" section of the report/UI - Section 21. Built
    directly from a real PredictionRecord's own distribution, never
    invented numbers."""

    player_id: int
    match_id: int
    season_year: int
    games_of_history: int
    predicted_mean: float
    actual: int
    interval_50: tuple[float, float]
    interval_80: tuple[float, float]
    prob_20_plus: float
    prob_25_plus: float
    prob_30_plus: float
    prob_35_plus: float
    category: str


def select_example_predictions(predictions: list[PredictionRecord], distribution: str = "nb") -> list[ExamplePrediction]:
    """Selects a small, deliberately non-cherry-picked spread of real
    predictions: one close to the median absolute error ("average"), one
    from the best-predicted decile ("strong"), one from the worst-predicted
    tail ("large miss" - included on purpose, not filtered out), plus one
    high-disposal-level and one low-disposal-level player, all picked by
    RANK on real prediction error / disposal level rather than searched
    for a flattering result."""
    scored = sorted(predictions, key=lambda p: abs(p.predicted_mean - p.actual))
    n = len(scored)
    if n == 0:
        return []

    picks = {
        "strong_prediction": scored[int(n * 0.1)],
        "average_prediction": scored[int(n * 0.5)],
        "large_miss": scored[int(n * 0.97)],
    }
    by_disposal_level_sorted = sorted(predictions, key=lambda p: p.actual)
    picks["high_disposal_player"] = by_disposal_level_sorted[int(len(by_disposal_level_sorted) * 0.95)]
    picks["low_disposal_player"] = by_disposal_level_sorted[int(len(by_disposal_level_sorted) * 0.05)]

    examples = []
    for category, p in picks.items():
        dist = p.nb_distribution() if distribution == "nb" else p.empirical_distribution()
        examples.append(
            ExamplePrediction(
                player_id=p.player_id,
                match_id=p.match_id,
                season_year=p.season_year,
                games_of_history=p.games_of_history,
                predicted_mean=p.predicted_mean,
                actual=p.actual,
                interval_50=dist.interval(0.5),
                interval_80=dist.interval(0.8),
                prob_20_plus=dist.prob_at_least(20),
                prob_25_plus=dist.prob_at_least(25),
                prob_30_plus=dist.prob_at_least(30),
                prob_35_plus=dist.prob_at_least(35),
                category=category,
            )
        )
    return examples
