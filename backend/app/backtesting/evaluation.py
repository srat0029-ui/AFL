"""Rigorous predictive-quality evaluation, distinct from (and built on top
of) app/backtesting/model_report.py's existing full-history report.

Two separations matter here that model_report.py doesn't make:

1. Warm-up vs. evaluation period. Every walk-forward prediction is already
   leakage-free (see elo_backtest.py/poisson_backtest.py), but predictions
   from the first season or two are still low-information — Elo starts
   every team at the same rating, Poisson's form/home-split factors start
   at neutral — so folding them into headline metrics at full weight
   understates how good the model actually is once it has real state built
   up. Matches before EVALUATION_START_YEAR are used to build that state
   ("warm-up") but excluded from the metrics presented as "how good is this
   model." This is a different concern from elo_tuning.py's tune/holdout
   split, which exists to keep hyperparameter *selection* honest — that
   split stays exactly as-is; this one is purely a reporting-time filter on
   top of predictions already generated the usual way.

2. Model quality vs. simple baselines (app/modelling/baselines.py) on the
   *same* evaluation set, so "Elo scores 0.19 Brier" can be read against
   "a baseline that always predicts the home team scores 0.24" rather than
   in isolation.

The current, still-in-progress season is reported separately again (section
3 of the brief this implements) rather than folded into "evaluation" or
excluded silently — it's real data, just not comparable to a complete season.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtesting.segments import BacktestSegment, build_segments, win_prob_metrics
from app.modelling.baselines import always_home_baseline, historical_home_win_rate_baseline, simple_form_baseline
from app.modelling.data_loading import load_completed_matches
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.metrics import bias, expected_calibration_error, favourite_calibration_table, mae, rmse
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import (
    PoissonConfig,
    central_interval,
    margin_distribution,
    score_distribution,
    total_points_distribution,
)
from app.models import ModelRun

# Chosen to match the brief: 2016-2018 is enough games for Elo ratings and
# Poisson's rolling/expanding factors to move well away from their neutral
# starting state without discarding so much history that the evaluation
# sample gets small. Adjustable per-call, not just a hardcoded assumption,
# since a different loaded date range should be able to override it.
EVALUATION_START_YEAR = 2019


class ModelsUnavailableError(Exception):
    """Raised when elo_cli.py / poisson_cli.py haven't been run yet."""


@dataclass(frozen=True)
class EvaluationPeriod:
    warmup_start_year: int
    warmup_end_year: int
    evaluation_start_year: int
    evaluation_end_year: int
    current_season_year: int
    n_warmup: int
    n_evaluation: int
    n_current_season: int


@dataclass(frozen=True)
class BaselineComparisonRow:
    name: str
    n: int
    metrics: dict[str, float]


def split_by_period(
    predictions: list, evaluation_start_year: int = EVALUATION_START_YEAR, current_year: int | None = None
) -> tuple[list, list, list, EvaluationPeriod]:
    """Splits already-generated walk-forward predictions into
    (warmup, evaluation, current_season) by season_year. Does not affect how
    the predictions were generated — pure post-hoc filtering."""
    if not predictions:
        empty_period = EvaluationPeriod(
            evaluation_start_year, evaluation_start_year - 1, evaluation_start_year, evaluation_start_year, evaluation_start_year, 0, 0, 0
        )
        return [], [], [], empty_period

    current_year = current_year or datetime.now(timezone.utc).year
    warmup = [p for p in predictions if p.season_year < evaluation_start_year]
    evaluation = [p for p in predictions if evaluation_start_year <= p.season_year < current_year]
    current_season = [p for p in predictions if p.season_year >= current_year]

    years = [p.season_year for p in predictions]
    period = EvaluationPeriod(
        warmup_start_year=min(years),
        warmup_end_year=evaluation_start_year - 1,
        evaluation_start_year=evaluation_start_year,
        evaluation_end_year=min(max(years), current_year - 1),
        current_season_year=current_year,
        n_warmup=len(warmup),
        n_evaluation=len(evaluation),
        n_current_season=len(current_season),
    )
    return warmup, evaluation, current_season, period


def build_baseline_comparison(
    matches, evaluation_match_ids: set[int], model_name: str, model_predictions: list
) -> list[BaselineComparisonRow]:
    """Scores the model itself plus all three baselines over exactly the
    same evaluation-period match set, so the comparison is apples-to-apples."""
    rows = []

    model_eval = [p for p in model_predictions if p.match_id in evaluation_match_ids]
    rows.append(
        BaselineComparisonRow(name=model_name, n=len(model_eval), metrics=win_prob_metrics(model_eval))
    )

    for name, baseline_fn in [
        ("baseline_always_home", always_home_baseline),
        ("baseline_historical_home_rate", historical_home_win_rate_baseline),
        ("baseline_simple_form", simple_form_baseline),
    ]:
        baseline_preds = baseline_fn(matches)
        baseline_eval = [p for p in baseline_preds if p.match_id in evaluation_match_ids]
        rows.append(BaselineComparisonRow(name=name, n=len(baseline_eval), metrics=win_prob_metrics(baseline_eval)))

    return rows


@dataclass(frozen=True)
class WinProbEvaluationReport:
    model_name: str
    period: EvaluationPeriod
    evaluation_metrics: dict[str, float]
    warmup_metrics: dict[str, float]
    full_history_metrics: dict[str, float]
    baseline_comparison: list[BaselineComparisonRow]
    calibration: list[dict]
    calibration_ece: float | None
    by_season: list[BacktestSegment]


def build_win_prob_evaluation(
    model_name: str,
    matches: list,
    predictions: list,
    evaluation_start_year: int = EVALUATION_START_YEAR,
) -> WinProbEvaluationReport:
    warmup, evaluation, _current, period = split_by_period(predictions, evaluation_start_year)
    evaluation_ids = {p.match_id for p in evaluation}

    calibration = favourite_calibration_table(
        [p.home_win_probability for p in evaluation], [p.actual_home_outcome for p in evaluation]
    )

    return WinProbEvaluationReport(
        model_name=model_name,
        period=period,
        evaluation_metrics=win_prob_metrics(evaluation),
        warmup_metrics=win_prob_metrics(warmup),
        full_history_metrics=win_prob_metrics(predictions),
        baseline_comparison=build_baseline_comparison(matches, evaluation_ids, model_name, predictions),
        calibration=calibration,
        calibration_ece=expected_calibration_error(calibration),
        by_season=build_segments(evaluation, key_fn=lambda p: str(p.season_year), metrics_fn=win_prob_metrics),
    )


def _scoring_metrics(predictions: list) -> dict[str, float]:
    if not predictions:
        return {}
    total_pred = [p.expected_total_points for p in predictions]
    total_actual = [p.actual_total_points for p in predictions]
    margin_pred = [p.expected_margin for p in predictions]
    margin_actual = [p.actual_margin for p in predictions]
    return {
        "total_points_mae": mae(total_pred, total_actual),
        "total_points_rmse": rmse(total_pred, total_actual),
        "total_points_bias": bias(total_pred, total_actual),
        "margin_mae": mae(margin_pred, margin_actual),
        "margin_rmse": rmse(margin_pred, margin_actual),
        "margin_bias": bias(margin_pred, margin_actual),
    }


def interval_coverage(predictions: list, config, coverage: float) -> dict[str, float]:
    """Fraction of matches where the actual total/margin fell inside the
    model's own `coverage`-width central prediction interval, reconstructed
    from the same lambdas used to build the point prediction — evaluating
    the full distribution's honesty, not just how close the mean was."""
    if not predictions:
        return {"total_hit_rate": float("nan"), "margin_hit_rate": float("nan")}

    total_hits = 0
    margin_hits = 0
    for p in predictions:
        home_pmf = score_distribution(p.home_expected_goals, p.home_expected_behinds, config.max_goals, config.max_behinds)
        away_pmf = score_distribution(p.away_expected_goals, p.away_expected_behinds, config.max_goals, config.max_behinds)

        total_pmf = total_points_distribution(home_pmf, away_pmf)
        total_lo, total_hi = central_interval(total_pmf, coverage)
        if total_lo <= p.actual_total_points <= total_hi:
            total_hits += 1

        margin_pmf, min_margin = margin_distribution(home_pmf, away_pmf)
        margin_lo, margin_hi = central_interval(margin_pmf, coverage, offset=min_margin)
        if margin_lo <= p.actual_margin <= margin_hi:
            margin_hits += 1

    n = len(predictions)
    return {"total_hit_rate": total_hits / n, "margin_hit_rate": margin_hits / n}


@dataclass(frozen=True)
class ScoringEvaluationReport:
    period: EvaluationPeriod
    evaluation_metrics: dict[str, float]
    warmup_metrics: dict[str, float]
    full_history_metrics: dict[str, float]
    by_season: list[BacktestSegment]
    # {"50pct": {"total_hit_rate": .., "margin_hit_rate": ..}, "80pct": {...}}
    # — a well-calibrated distribution should hit its own 50%/80% interval
    # roughly 50%/80% of the time, no more, no less.
    interval_coverage: dict[str, dict[str, float]]


def build_scoring_evaluation(
    matches: list,
    predictions: list,
    config,
    evaluation_start_year: int = EVALUATION_START_YEAR,
) -> ScoringEvaluationReport:
    warmup, evaluation, _current, period = split_by_period(predictions, evaluation_start_year)

    return ScoringEvaluationReport(
        period=period,
        evaluation_metrics=_scoring_metrics(evaluation),
        warmup_metrics=_scoring_metrics(warmup),
        full_history_metrics=_scoring_metrics(predictions),
        by_season=build_segments(evaluation, key_fn=lambda p: str(p.season_year), metrics_fn=_scoring_metrics),
        interval_coverage={
            "50pct": interval_coverage(evaluation, config, 0.5),
            "80pct": interval_coverage(evaluation, config, 0.8),
        },
    )


def load_elo_evaluation(db: Session) -> tuple[ModelRun, WinProbEvaluationReport]:
    elo_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "elo"))
    if elo_run is None:
        raise ModelsUnavailableError("Run `python -m app.modelling.elo_cli` first.")

    matches = load_completed_matches(db)
    config = EloConfig(**elo_run.config_json)
    predictions = elo_walk_forward(matches, config)
    report = build_win_prob_evaluation("elo", matches, predictions)
    return elo_run, report


def load_poisson_evaluation(db: Session) -> tuple[ModelRun, WinProbEvaluationReport, ScoringEvaluationReport]:
    poisson_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson"))
    if poisson_run is None:
        raise ModelsUnavailableError("Run `python -m app.modelling.poisson_cli` first.")

    matches = load_completed_matches(db)
    config = PoissonConfig(**poisson_run.config_json)
    predictions = poisson_walk_forward(matches, config)
    win_report = build_win_prob_evaluation("poisson", matches, predictions)
    scoring_report = build_scoring_evaluation(matches, predictions, config)
    return poisson_run, win_report, scoring_report
