"""Original vs revised Poisson scoring-model comparison.

"Original" is whatever config is currently persisted as the "poisson"
ModelRun (produced by poisson_cli.py's existing tune/holdout selection,
predating the league_window_games field — so PoissonConfig(**config_json)
picks up the default league_window_games=None, i.e. the original unbounded
expanding league-average behaviour, bit for bit).

"Revised" is a config picked by the exact same tune/holdout selection
procedure (poisson_tuning.select_best_config), just against the extended
grid that now includes league_window_games choices. This is deliberately
NOT a hand-picked "just change league_window_games and hold everything else
fixed" comparison — the revised config is a fresh, fully-retuned selection,
consistent with how the original config was itself chosen. Both configs are
then replayed over the *same* match history and compared on the same
metrics, so any difference is attributable to the model, not to different
input data.

Special attention throughout to the 2021 season (the anomaly this fix
targets) and to season-opening rounds generally (rounds 1-3, rounds 1-5),
since a league-wide baseline shock is exactly the kind of error that shows
up most sharply in the first few rounds of a season, before that season's
own matches have had a chance to update team-level state.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtesting.evaluation import EVALUATION_START_YEAR, EvaluationPeriod, ModelsUnavailableError, interval_coverage, split_by_period
from app.backtesting.segments import BacktestSegment, build_segments
from app.modelling.data_loading import load_completed_matches
from app.modelling.metrics import accuracy, bias, brier_score, log_loss, mae
from app.modelling.poisson_backtest import run_walk_forward
from app.modelling.poisson_model import PoissonConfig
from app.modelling.poisson_tuning import DEFAULT_GRID, select_best_config
from app.modelling.promotion import PromotionDecision
from app.models import ModelRun

DEFAULT_TUNE_END_YEAR = 2022
_ROUND_BANDS = [("rounds_1_3", 1, 3), ("rounds_1_5", 1, 5)]

# Thresholds for the scoring-model promotion rule below. Deliberately
# separate from app/modelling/promotion.py's win-prob-model rule (Brier/log
# loss/ECE don't apply the same way here — this model's flagship market is
# total points, scored on MAE) but the same spirit: every check must pass,
# not just one flattering number, and the default when evidence is mixed is
# to keep the original.
_MIN_2021_MAE_IMPROVEMENT_PCT = 0.10  # the whole point of the fix — must be a clear, not marginal, win
_MAX_BRIER_REGRESSION_PCT = 0.05  # winner-probability quality (a secondary market for this model) must not meaningfully worsen


def _combined_metrics(predictions: list) -> dict[str, float]:
    """total/margin MAE + bias, plus winner brier/log-loss/accuracy, in one
    table — this report cares about scoring accuracy and winner accuracy
    together at every breakdown (season, round band), so one merged metrics
    dict per group is more useful here than the separate scoring/win-prob
    dataclasses evaluation.py uses for its single-model reports."""
    if not predictions:
        return {}
    total_pred = [p.expected_total_points for p in predictions]
    total_actual = [p.actual_total_points for p in predictions]
    margin_pred = [p.expected_margin for p in predictions]
    margin_actual = [p.actual_margin for p in predictions]
    probs = [p.home_win_probability for p in predictions]
    outcomes = [p.actual_home_outcome for p in predictions]
    return {
        "total_points_mae": mae(total_pred, total_actual),
        "total_points_bias": bias(total_pred, total_actual),
        "margin_mae": mae(margin_pred, margin_actual),
        "margin_bias": bias(margin_pred, margin_actual),
        "brier_score": brier_score(probs, outcomes),
        "log_loss": log_loss(probs, outcomes),
        "accuracy": accuracy(probs, outcomes),
    }


@dataclass(frozen=True)
class RoundBandMetrics:
    label: str
    n: int
    metrics: dict[str, float]


def _round_bands(predictions: list) -> list[RoundBandMetrics]:
    bands = [
        RoundBandMetrics(
            label=label,
            n=len([p for p in predictions if lo <= p.round_number <= hi]),
            metrics=_combined_metrics([p for p in predictions if lo <= p.round_number <= hi]),
        )
        for label, lo, hi in _ROUND_BANDS
    ]
    bands.append(RoundBandMetrics(label="full_season", n=len(predictions), metrics=_combined_metrics(predictions)))
    return bands


@dataclass(frozen=True)
class PoissonVariantReport:
    label: str  # "original" | "revised"
    config: PoissonConfig
    period: EvaluationPeriod
    evaluation_metrics: dict[str, float]
    warmup_metrics: dict[str, float]
    full_history_metrics: dict[str, float]
    by_season: list[BacktestSegment]
    early_season_bands: list[RoundBandMetrics]  # across the whole evaluation period
    season_2021_bands: list[RoundBandMetrics]  # same breakdown, restricted to 2021 only
    interval_coverage: dict[str, dict[str, float]]


def _build_variant_report(label: str, config: PoissonConfig, matches: list, predictions: list) -> PoissonVariantReport:
    warmup, evaluation, _current, period = split_by_period(predictions, EVALUATION_START_YEAR)
    season_2021 = [p for p in evaluation if p.season_year == 2021]

    return PoissonVariantReport(
        label=label,
        config=config,
        period=period,
        evaluation_metrics=_combined_metrics(evaluation),
        warmup_metrics=_combined_metrics(warmup),
        full_history_metrics=_combined_metrics(predictions),
        by_season=build_segments(evaluation, key_fn=lambda p: str(p.season_year), metrics_fn=_combined_metrics),
        early_season_bands=_round_bands(evaluation),
        season_2021_bands=_round_bands(season_2021),
        interval_coverage={
            "50pct": interval_coverage(evaluation, config, 0.5),
            "80pct": interval_coverage(evaluation, config, 0.8),
        },
    )


def evaluate_poisson_promotion_rule(original: PoissonVariantReport, revised: PoissonVariantReport) -> PromotionDecision:
    checks: list[tuple[bool, str]] = []

    orig_eval_mae = original.evaluation_metrics.get("total_points_mae", float("inf"))
    rev_eval_mae = revised.evaluation_metrics.get("total_points_mae", float("inf"))
    beats_original_mae = rev_eval_mae < orig_eval_mae
    checks.append((beats_original_mae, f"beats original on evaluation-period total-points MAE ({rev_eval_mae:.2f} vs {orig_eval_mae:.2f})"))

    orig_2021_mae = next(b for b in original.season_2021_bands if b.label == "full_season").metrics.get("total_points_mae", float("inf"))
    rev_2021_mae = next(b for b in revised.season_2021_bands if b.label == "full_season").metrics.get("total_points_mae", float("inf"))
    improvement_2021_pct = (orig_2021_mae - rev_2021_mae) / orig_2021_mae if orig_2021_mae > 0 else 0.0
    fixes_2021 = improvement_2021_pct >= _MIN_2021_MAE_IMPROVEMENT_PCT
    checks.append(
        (fixes_2021, f"clearly improves the 2021 anomaly by >= {_MIN_2021_MAE_IMPROVEMENT_PCT:.0%} MAE ({improvement_2021_pct:.1%} actual: {orig_2021_mae:.2f} -> {rev_2021_mae:.2f})")
    )

    orig_brier = original.evaluation_metrics.get("brier_score", float("inf"))
    rev_brier = revised.evaluation_metrics.get("brier_score", float("inf"))
    brier_ok = rev_brier <= orig_brier * (1 + _MAX_BRIER_REGRESSION_PCT)
    checks.append((brier_ok, f"does not materially worsen winner-probability Brier score ({rev_brier:.4f} vs {orig_brier:.4f})"))

    orig_by_season = {s.label: s.metrics.get("total_points_mae", float("inf")) for s in original.by_season}
    rev_by_season = {s.label: s.metrics.get("total_points_mae", float("inf")) for s in revised.by_season}
    common_seasons = sorted(set(orig_by_season) & set(rev_by_season))
    n_improved = sum(1 for s in common_seasons if rev_by_season[s] < orig_by_season[s])
    majority_improved = len(common_seasons) > 0 and n_improved >= (len(common_seasons) // 2 + 1)
    checks.append((majority_improved, f"improves total-points MAE in a majority of evaluation seasons ({n_improved}/{len(common_seasons)})"))

    promote = all(passed for passed, _ in checks)
    reasons = [f"{'PASS' if passed else 'FAIL'}: {desc}" for passed, desc in checks]
    return PromotionDecision(promote=promote, reasons=reasons)


@dataclass(frozen=True)
class PoissonRevisionComparison:
    original: PoissonVariantReport
    revised: PoissonVariantReport
    tune_leaderboard_top5: list[dict]
    common_match_count: int
    revised_beats_original_2021: bool
    revised_worse_than_original_full_history: bool
    promotion: PromotionDecision


def build_poisson_revision_comparison(db: Session, tune_end_year: int = DEFAULT_TUNE_END_YEAR) -> PoissonRevisionComparison:
    original_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson"))
    if original_run is None:
        raise ModelsUnavailableError("Run `python -m app.modelling.poisson_cli` first.")
    original_config = PoissonConfig(**original_run.config_json)

    matches = load_completed_matches(db)
    tune_matches = [m for m in matches if m.season_year <= tune_end_year]
    revised_config, leaderboard = select_best_config(tune_matches, DEFAULT_GRID)

    original_predictions = run_walk_forward(matches, original_config)
    revised_predictions = run_walk_forward(matches, revised_config)

    common_match_count = len(
        {p.match_id for p in original_predictions} & {p.match_id for p in revised_predictions}
    )

    original_report = _build_variant_report("original", original_config, matches, original_predictions)
    revised_report = _build_variant_report("revised", revised_config, matches, revised_predictions)

    original_2021_mae = original_report.season_2021_bands[-1].metrics.get("total_points_mae", float("inf"))
    revised_2021_mae = revised_report.season_2021_bands[-1].metrics.get("total_points_mae", float("inf"))
    original_full_mae = original_report.full_history_metrics.get("total_points_mae", float("inf"))
    revised_full_mae = revised_report.full_history_metrics.get("total_points_mae", float("inf"))

    return PoissonRevisionComparison(
        original=original_report,
        revised=revised_report,
        tune_leaderboard_top5=[
            {"config": row["config"], "tune_total_points_mae": row["tune_total_points_mae"]}
            for row in leaderboard[:5]
        ],
        common_match_count=common_match_count,
        revised_beats_original_2021=revised_2021_mae < original_2021_mae,
        revised_worse_than_original_full_history=revised_full_mae > original_full_mae,
        promotion=evaluate_poisson_promotion_rule(original_report, revised_report),
    )
