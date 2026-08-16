"""Deeper goal-model diagnostics beyond headline MAE/calibration -
Sections 14-20 of the goal-prediction stage brief: hurdle-vs-NB zero-
handling, player-history/scoring-archetype buckets, feature ablations,
team-goal consistency, ranking quality, TOG, and 2020 treatment.
"""

from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from app.player_modelling.goal_backtest import GoalDatasetSplit, GoalPredictionRecord, build_goal_dataset_from_rows, run_goal_candidate_models
from app.player_modelling.goal_evaluation import compute_point_metrics, compute_threshold_metrics, compute_zero_goal_calibration
from app.player_modelling.goal_features import PLAYER_FEATURE_NAMES

HISTORY_BUCKETS = (("<10", 0, 10), ("10-30", 10, 30), ("30-75", 30, 75), ("75+", 75, 10_000))


def by_history_bucket(predictions: list[GoalPredictionRecord]) -> dict[str, dict]:
    rows = []
    for label, lo, hi in HISTORY_BUCKETS:
        bucket = [p for p in predictions if lo <= p.games_of_history < hi]
        if not bucket:
            rows.append((label, None))
            continue
        pm = compute_point_metrics(bucket)
        t1 = compute_threshold_metrics(bucket, 1)
        rows.append((label, {"n": pm.n, "mae": pm.mae, "rmse": pm.rmse, "bias": pm.bias, "brier_1plus": t1.brier}))
    return dict(rows)


# --- Scoring archetypes (Section 18): purely from historical scoring RATE,
# never an invented position label. Buckets are cut on each player's
# CAREER-TO-DATE goals-per-game as of that row (already a point-in-time
# feature - "goals_career_avg") so the grouping itself can't leak future
# scoring into the bucket assignment.
ARCHETYPE_BUCKETS = (
    ("very_low (<0.15/g)", 0.0, 0.15),
    ("occasional (0.15-0.4/g)", 0.15, 0.4),
    ("regular (0.4-0.8/g)", 0.4, 0.8),
    ("high_volume (0.8+/g)", 0.8, 100.0),
)


def by_scoring_archetype(predictions: list[GoalPredictionRecord], eval_rows) -> dict[str, dict]:
    rate_by_key = {(r.match_id, r.player_id): r.features.get("goals_career_avg") for r in eval_rows}
    rows = []
    for label, lo, hi in ARCHETYPE_BUCKETS:
        bucket = [p for p in predictions if (rate_by_key.get((p.match_id, p.player_id)) or 0) >= lo and (rate_by_key.get((p.match_id, p.player_id)) or 0) < hi]
        if not bucket:
            rows.append((label, None))
            continue
        pm = compute_point_metrics(bucket)
        t1 = compute_threshold_metrics(bucket, 1)
        rows.append((label, {"n": pm.n, "mae": pm.mae, "bias": pm.bias, "brier_1plus": t1.brier, "ece_1plus": t1.ece}))
    return dict(rows)


@dataclass(frozen=True)
class HurdleVsNBComparison:
    nb_zero_brier: float
    hurdle_zero_brier: float
    nb_zero_ece: float | None
    hurdle_zero_ece: float | None
    nb_1plus_ece: float | None
    hurdle_1plus_ece: float | None


def compare_hurdle_vs_nb(nb_predictions: list[GoalPredictionRecord], hurdle_predictions: list[GoalPredictionRecord]) -> HurdleVsNBComparison:
    """Section 7's explicit instruction: only keep the extra hurdle
    machinery if it demonstrably improves held-out probability metrics
    over a plain single-process NB. Compares both on the SAME eval rows."""
    nb_zero = compute_zero_goal_calibration(nb_predictions)
    hurdle_zero = compute_zero_goal_calibration(hurdle_predictions)
    nb_1p = compute_threshold_metrics(nb_predictions, 1)
    hurdle_1p = compute_threshold_metrics(hurdle_predictions, 1)
    return HurdleVsNBComparison(
        nb_zero_brier=nb_zero.brier,
        hurdle_zero_brier=hurdle_zero.brier,
        nb_zero_ece=nb_zero.ece,
        hurdle_zero_ece=hurdle_zero.ece,
        nb_1plus_ece=nb_1p.ece,
        hurdle_1plus_ece=hurdle_1p.ece,
    )


TOG_FEATURE_NAMES = ("tog_last5_avg",)


@dataclass(frozen=True)
class GoalTOGComparison:
    with_tog_mae: float
    without_tog_mae: float
    high_variance_tog_mae: float | None
    stable_tog_mae: float | None


def compare_with_without_tog(split: GoalDatasetSplit) -> GoalTOGComparison:
    full_features = PLAYER_FEATURE_NAMES
    no_tog_features = tuple(f for f in PLAYER_FEATURE_NAMES if f not in TOG_FEATURE_NAMES)

    with_tog = run_goal_candidate_models(split, feature_names=full_features, model_names=("negative_binomial",))["negative_binomial"]
    without_tog = run_goal_candidate_models(split, feature_names=no_tog_features, model_names=("negative_binomial",))["negative_binomial"]

    with_pm = compute_point_metrics(with_tog)
    without_pm = compute_point_metrics(without_tog)

    tog_values = [p.tog_last5_avg for p in with_tog if p.tog_last5_avg is not None]
    if tog_values:
        low_cut = float(np.percentile(tog_values, 25))
        low_tog = [p for p in with_tog if p.tog_last5_avg is not None and p.tog_last5_avg <= low_cut]
        stable_tog = [p for p in with_tog if p.tog_last5_avg is not None and p.tog_last5_avg > low_cut]
        high_var_mae = compute_point_metrics(low_tog).mae if low_tog else None
        stable_mae = compute_point_metrics(stable_tog).mae if stable_tog else None
    else:
        high_var_mae = stable_mae = None

    return GoalTOGComparison(
        with_tog_mae=with_pm.mae, without_tog_mae=without_pm.mae, high_variance_tog_mae=high_var_mae, stable_tog_mae=stable_mae
    )


GOAL_FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "scoring_history": (
        "goals_last3_avg", "goals_last5_avg", "goals_last10_avg", "goals_season_avg", "goals_career_avg",
        "goals_ewma", "goals_last5_std", "conversion_rate_career", "conversion_rate_last10",
        "zero_goal_rate_last10", "rate_1plus_last10", "rate_2plus_last10", "rate_3plus_last10",
    ),
    "opportunity": ("marks_inside_50_last5_avg", "inside_50s_last5_avg", "scoring_shots_last5_avg", "goal_assists_last5_avg", "marks_last5_avg", "disposals_last5_avg"),
    "team_expected_score": ("team_expected_score", "team_elo_win_prob", "expected_margin", "team_recent_goals_avg", "team_recent_inside50_avg"),
    "opponent_defence": ("opponent_goals_conceded_avg", "opponent_scoring_shots_conceded_avg", "opponent_expected_score"),
    "venue": ("venue_goals_env",),
    "tog": TOG_FEATURE_NAMES,
}


def run_goal_ablations(split: GoalDatasetSplit) -> dict[str, dict]:
    base = GOAL_FEATURE_GROUPS["scoring_history"]
    combos = {
        "scoring_history_only": base,
        "scoring_history_plus_opportunity": base + GOAL_FEATURE_GROUPS["opportunity"],
        "scoring_history_plus_team_expected": base + GOAL_FEATURE_GROUPS["team_expected_score"],
        "scoring_history_plus_opponent": base + GOAL_FEATURE_GROUPS["opponent_defence"],
        "scoring_history_plus_venue": base + GOAL_FEATURE_GROUPS["venue"],
        "scoring_history_plus_elo_margin": base + ("team_elo_win_prob", "expected_margin"),
        "all_features": PLAYER_FEATURE_NAMES,
    }
    results = {}
    for combo_name, feature_names in combos.items():
        preds = run_goal_candidate_models(split, feature_names=feature_names, model_names=("negative_binomial",))["negative_binomial"]
        pm = compute_point_metrics(preds)
        t1 = compute_threshold_metrics(preds, 1)
        results[combo_name] = {"n_features": len(feature_names), "mae": pm.mae, "rmse": pm.rmse, "ece_1plus": t1.ece}
    return results


SHORTENED_SEASON_YEAR = 2020


def compare_2020_handling(player_rows, team_rows, team_context) -> dict[str, dict]:
    unadjusted = build_goal_dataset_from_rows(player_rows, team_rows, team_context, season_scale_factors=None)

    # From the goal audit: 2020 avg goals 0.403 vs 0.587/0.507 in 2019/2021 - adjacent avg 0.547, ratio ~1.357.
    adjacent_avg = (0.531 + 0.507) / 2
    scale = adjacent_avg / 0.403
    adjusted = build_goal_dataset_from_rows(player_rows, team_rows, team_context, season_scale_factors={SHORTENED_SEASON_YEAR: scale})

    def _score(split: GoalDatasetSplit) -> dict:
        preds = run_goal_candidate_models(split, model_names=("negative_binomial",))["negative_binomial"]
        overall = compute_point_metrics(preds)
        round_by_key = {(r.match_id, r.player_id): r.round_number for r in split.eval_rows}
        early_2021 = [p for p in preds if p.season_year == 2021 and round_by_key.get((p.match_id, p.player_id), 999) <= 6]
        early_metrics = compute_point_metrics(early_2021) if early_2021 else None
        return {
            "overall_mae": overall.mae,
            "early_2021_n": len(early_2021),
            "early_2021_mae": early_metrics.mae if early_metrics else None,
        }

    return {"scale_factor_used": scale, "unadjusted": _score(unadjusted), "scaled_2020_history": _score(adjusted)}


@dataclass(frozen=True)
class TeamGoalConsistency:
    """Section 15: does the sum of individual player expected goals for a
    team roughly match that team's own expected total score (converted to
    an expected-goals figure via the existing Poisson model)? Measured, not
    forced - a large, systematic gap would suggest the player-level model
    and the team-level Poisson model disagree about the scoring
    environment; a small one means they're reasonably mutually consistent
    without any explicit reconciliation step."""

    n_team_matches: int
    mean_sum_predicted: float
    mean_team_expected_goals: float
    mean_absolute_gap: float
    mean_signed_gap: float  # positive = player-level sum overshoots the team total


def measure_team_goal_consistency(predictions: list[GoalPredictionRecord], eval_rows, team_context: dict[int, dict[int, dict]]) -> TeamGoalConsistency:
    pred_by_key = {(p.match_id, p.player_id): p.predicted_mean for p in predictions}
    sums_by_team_match: dict[tuple[int, int], float] = defaultdict(float)
    for r in eval_rows:
        val = pred_by_key.get((r.match_id, r.player_id))
        if val is not None:
            sums_by_team_match[(r.match_id, r.team_id)] += val

    gaps = []
    signed_gaps = []
    team_expected_goals_values = []
    for (match_id, team_id), summed in sums_by_team_match.items():
        ctx = team_context.get(match_id, {}).get(team_id, {})
        team_expected_score = ctx.get("expected_score")
        if team_expected_score is None:
            continue
        team_expected_goals = team_expected_score / 6.0  # points -> goals (6 points/goal), matching the Poisson model's own scoring convention
        gaps.append(abs(summed - team_expected_goals))
        signed_gaps.append(summed - team_expected_goals)
        team_expected_goals_values.append(team_expected_goals)

    if not gaps:
        return TeamGoalConsistency(0, float("nan"), float("nan"), float("nan"), float("nan"))
    return TeamGoalConsistency(
        n_team_matches=len(gaps),
        mean_sum_predicted=sum(sums_by_team_match.values()) / len(sums_by_team_match),
        mean_team_expected_goals=sum(team_expected_goals_values) / len(team_expected_goals_values),
        mean_absolute_gap=sum(gaps) / len(gaps),
        mean_signed_gap=sum(signed_gaps) / len(signed_gaps),
    )


@dataclass(frozen=True)
class RankingQuality:
    n_matches: int
    top1_hit_rate: float  # fraction of matches where the single highest-projected scorer was AMONG the actual top scorers (ties included)
    top2_capture_rate: float  # fraction of the match's actual top-2 goalkickers captured by the model's top-2 projected
    top3_capture_rate: float


def evaluate_ranking_quality(predictions: list[GoalPredictionRecord]) -> RankingQuality:
    """Section 14: for each match, does the model's ranking of projected
    scorers line up with who actually kicked the most goals? Secondary to
    calibration, but a useful sanity check for the eventual product use
    case (highlighting likely scorers)."""
    by_match: dict[int, list[GoalPredictionRecord]] = defaultdict(list)
    for p in predictions:
        by_match[p.match_id].append(p)

    top1_hits = top2_num = top2_den = top3_num = top3_den = 0
    n_matches = 0
    for match_id, preds in by_match.items():
        if len(preds) < 3:
            continue
        n_matches += 1
        actual_sorted = sorted(preds, key=lambda p: p.actual, reverse=True)
        projected_sorted = sorted(preds, key=lambda p: p.predicted_mean, reverse=True)

        max_actual = actual_sorted[0].actual
        actual_top_scorers = {p.player_id for p in preds if p.actual == max_actual}
        if projected_sorted[0].player_id in actual_top_scorers:
            top1_hits += 1

        actual_top2_ids = {p.player_id for p in actual_sorted[:2]}
        projected_top2_ids = {p.player_id for p in projected_sorted[:2]}
        top2_num += len(actual_top2_ids & projected_top2_ids)
        top2_den += len(actual_top2_ids)

        actual_top3_ids = {p.player_id for p in actual_sorted[:3]}
        projected_top3_ids = {p.player_id for p in projected_sorted[:3]}
        top3_num += len(actual_top3_ids & projected_top3_ids)
        top3_den += len(actual_top3_ids)

    return RankingQuality(
        n_matches=n_matches,
        top1_hit_rate=top1_hits / n_matches if n_matches else float("nan"),
        top2_capture_rate=top2_num / top2_den if top2_den else float("nan"),
        top3_capture_rate=top3_num / top3_den if top3_den else float("nan"),
    )


@dataclass(frozen=True)
class GoalExamplePrediction:
    player_id: int
    match_id: int
    season_year: int
    games_of_history: int
    predicted_mean: float
    actual: int
    prob_1_plus: float
    prob_2_plus: float
    prob_3_plus: float
    prob_4_plus: float
    category: str


def select_goal_example_predictions(predictions: list[GoalPredictionRecord]) -> list[GoalExamplePrediction]:
    """Section 22: a deliberately non-cherry-picked spread - a confident
    correct-zero prediction, a strong hit, a 2+/3+ case, a real miss, and
    an inexperienced player - picked by RANK on real prediction error /
    real scoring outcomes, not searched for a flattering result."""
    scored = sorted(predictions, key=lambda p: abs(p.predicted_mean - p.actual))
    n = len(scored)
    if n == 0:
        return []

    zero_correct = next((p for p in predictions if p.actual == 0 and p.distribution().pmf_at(0) > 0.8), scored[0])
    strong_forward = next((p for p in sorted(predictions, key=lambda p: -p.actual) if p.games_of_history >= 30), scored[0])
    two_plus = next((p for p in predictions if p.actual >= 2), scored[int(n * 0.5)])
    three_plus = next((p for p in predictions if p.actual >= 3), scored[int(n * 0.7)])
    large_miss = scored[int(n * 0.995)]
    inexperienced = next((p for p in predictions if p.games_of_history < 10), scored[0])

    picks = {
        "correctly_predicted_unlikely_to_score": zero_correct,
        "strong_forward_prediction": strong_forward,
        "2plus_goal_case": two_plus,
        "3plus_goal_case": three_plus,
        "major_miss": large_miss,
        "inexperienced_player": inexperienced,
    }

    examples = []
    for category, p in picks.items():
        dist = p.distribution()
        examples.append(
            GoalExamplePrediction(
                player_id=p.player_id,
                match_id=p.match_id,
                season_year=p.season_year,
                games_of_history=p.games_of_history,
                predicted_mean=p.predicted_mean,
                actual=p.actual,
                prob_1_plus=dist.prob_at_least(1),
                prob_2_plus=dist.prob_at_least(2),
                prob_3_plus=dist.prob_at_least(3),
                prob_4_plus=dist.prob_at_least(4),
                category=category,
            )
        )
    return examples
