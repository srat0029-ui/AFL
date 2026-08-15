"""Ties together Stage 1B/1C's logistic-regression evaluation: fitting,
baseline/Elo/Poisson comparison, calibration, ablation, bootstrap
uncertainty, season stability, disagreement vs Elo, and the promotion
decision — for both the "stats only" and "stats + Elo" variants, always
over the identical evaluation match set (the Stage brief's explicit
"compare over the EXACT SAME MATCH SET" requirement).

Regularisation strength and calibration method are expected to already be
selected via app/modelling/logistic_cli.py's tuning pass and persisted to
ModelRun for reproducibility/documentation. This module still *re-derives*
the actual fitted calibrator live each call (via the same deterministic
inner tune/validation split logistic_tuning.py uses for C) rather than
trying to reconstruct a fitted sklearn object from a persisted string —
consistent with the "compute live, not persisted" approach already used
for the rest of this project's backtesting (app/backtesting/model_report.py,
evaluation.py), and just as fast: everything here fits in well under a
second even with a 2000-resample bootstrap.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtesting.evaluation import EVALUATION_START_YEAR, split_by_period
from app.backtesting.logistic_comparison import LogisticComparisonReport, build_logistic_vs_elo_comparison
from app.backtesting.segments import BacktestSegment, win_prob_metrics_from_pairs
from app.modelling.ablation import (
    AblationResult,
    permutation_importance,
    run_feature_group_ablation,
    run_single_feature_ablation,
    standardized_coefficients,
)
from app.modelling.baselines import always_home_baseline, historical_home_win_rate_baseline, simple_form_baseline
from app.modelling.bootstrap import BootstrapResult, bootstrap_metric_difference
from app.modelling.calibration_methods import select_calibration_method
from app.modelling.data_loading import load_completed_matches, load_matches_with_team_stats
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.features import STATS_FEATURE_NAMES, STATS_PLUS_ELO_FEATURE_NAMES, MatchFeatureRow, build_match_features
from app.modelling.logistic import LogisticConfig, fit_logistic_model, predict
from app.modelling.metrics import brier_score, expected_calibration_error, favourite_calibration_table, log_loss
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import PoissonConfig
from app.modelling.promotion import PromotionDecision, evaluate_promotion_rule
from app.models import ModelRun

INNER_VALIDATION_START_YEAR = 2018  # last year of the tune window used as the inner out-of-sample check


class ModelsUnavailableError(Exception):
    """Raised when elo_cli.py / poisson_cli.py / logistic_cli.py haven't been run yet."""


@dataclass(frozen=True)
class BaselineRow:
    name: str
    n: int
    brier_score: float
    log_loss: float
    accuracy: float


@dataclass(frozen=True)
class LogisticVariantReport:
    variant: str  # "stats_only" | "stats_plus_elo"
    feature_names: tuple[str, ...]
    C: float
    calibration_method: str
    n_eval: int
    brier_score: float
    log_loss: float
    accuracy: float
    calibration: list[dict]
    calibration_ece: float | None
    standardized_coefficients: dict[str, float]
    permutation_importance: dict[str, float]
    single_feature_ablation: dict[str, float]
    feature_group_ablation: list[AblationResult]
    bootstrap_vs_elo: BootstrapResult
    by_season: list[BacktestSegment]
    disagreement_vs_elo: LogisticComparisonReport
    promotion: PromotionDecision


@dataclass(frozen=True)
class LogisticComparisonOverview:
    n_eval: int
    evaluation_start_year: int
    evaluation_end_year: int
    baselines: list[BaselineRow]
    elo: BaselineRow
    poisson: BaselineRow
    stats_only: LogisticVariantReport
    stats_plus_elo: LogisticVariantReport


def _score(name: str, probs: list[float], outcomes: list[float]) -> BaselineRow:
    return BaselineRow(
        name=name, n=len(probs), brier_score=brier_score(probs, outcomes), log_loss=log_loss(probs, outcomes),
        accuracy=win_prob_metrics_from_pairs(probs, outcomes).get("accuracy", float("nan")),
    )


def _by_season(rows: list[MatchFeatureRow], probs: list[float]) -> list[BacktestSegment]:
    groups: dict[str, list[tuple[float, float]]] = {}
    for r, p in zip(rows, probs):
        groups.setdefault(str(r.season_year), []).append((p, r.actual_home_outcome))
    segments = []
    for label, pairs in groups.items():
        ps = [p for p, _ in pairs]
        os = [o for _, o in pairs]
        segments.append(BacktestSegment(label=label, n=len(pairs), metrics=win_prob_metrics_from_pairs(ps, os)))
    return sorted(segments, key=lambda s: s.label)


def _build_variant_report(
    variant: str,
    feature_names: tuple[str, ...],
    tune_rows: list[MatchFeatureRow],
    eval_rows: list[MatchFeatureRow],
    C: float,
    elo_probs_by_match: dict[int, float],
    elo_eval_brier: float,
    elo_eval_log_loss: float,
) -> LogisticVariantReport:
    full_tune = [r for r in tune_rows if r.has_full_history]
    config = LogisticConfig(feature_names=feature_names, C=C)
    pipeline = fit_logistic_model(full_tune, config)

    preds = predict(pipeline, eval_rows, feature_names)
    raw_probs = [p.home_win_probability for p in preds]
    outcomes = [p.actual_home_outcome for p in preds]

    # Calibration: fit an inner model (train-only part of the tune window),
    # score it on the held-out remainder of the tune window, select a
    # calibrator from THAT out-of-sample check, then apply it to the final
    # model's evaluation-period output — see module docstring.
    inner_train = [r for r in tune_rows if r.season_year < INNER_VALIDATION_START_YEAR and r.has_full_history]
    inner_val = [r for r in tune_rows if r.season_year >= INNER_VALIDATION_START_YEAR and r.has_full_history]
    inner_pipeline = fit_logistic_model(inner_train, config)
    inner_preds = predict(inner_pipeline, inner_val, feature_names)
    calibrator, _calibration_scores = select_calibration_method(
        [p.home_win_probability for p in inner_preds], [p.actual_home_outcome for p in inner_preds]
    )
    calibrated_probs = calibrator.apply(raw_probs)

    calibration_table = favourite_calibration_table(calibrated_probs, outcomes)

    elo_eval_probs = [elo_probs_by_match[r.match_id] for r in eval_rows]
    bootstrap = bootstrap_metric_difference(elo_eval_probs, calibrated_probs, outcomes, brier_score)

    season_labels = sorted({r.season_year for r in eval_rows})
    season_improvements = []
    for year in season_labels:
        idx = [i for i, r in enumerate(eval_rows) if r.season_year == year]
        season_elo_brier = brier_score([elo_eval_probs[i] for i in idx], [outcomes[i] for i in idx])
        season_logistic_brier = brier_score([calibrated_probs[i] for i in idx], [outcomes[i] for i in idx])
        season_improvements.append(season_logistic_brier < season_elo_brier)

    promotion = evaluate_promotion_rule(
        candidate_brier=brier_score(calibrated_probs, outcomes),
        elo_brier=elo_eval_brier,
        candidate_log_loss=log_loss(calibrated_probs, outcomes),
        elo_log_loss=elo_eval_log_loss,
        candidate_ece=expected_calibration_error(calibration_table),
        brier_improvement_bootstrap=bootstrap,
        season_brier_improvements=season_improvements,
    )

    return LogisticVariantReport(
        variant=variant,
        feature_names=feature_names,
        C=C,
        calibration_method=calibrator.method,
        n_eval=len(eval_rows),
        brier_score=brier_score(calibrated_probs, outcomes),
        log_loss=log_loss(calibrated_probs, outcomes),
        accuracy=win_prob_metrics_from_pairs(calibrated_probs, outcomes).get("accuracy", float("nan")),
        calibration=calibration_table,
        calibration_ece=expected_calibration_error(calibration_table),
        standardized_coefficients=standardized_coefficients(pipeline, feature_names),
        permutation_importance=permutation_importance(pipeline, eval_rows, feature_names),
        single_feature_ablation=run_single_feature_ablation(tune_rows, eval_rows, feature_names, C),
        feature_group_ablation=run_feature_group_ablation(tune_rows, eval_rows, C, elo_alone_brier=elo_eval_brier),
        bootstrap_vs_elo=bootstrap,
        by_season=_by_season(eval_rows, calibrated_probs),
        disagreement_vs_elo=build_logistic_vs_elo_comparison(elo_probs_by_match, preds),
        promotion=promotion,
    )


def build_logistic_comparison(db: Session, C_stats_only: float, C_stats_plus_elo: float) -> LogisticComparisonOverview:
    """Requires elo_cli.py and poisson_cli.py to have already been run
    (their persisted, tuned configs are used — this never re-derives its
    own Elo/Poisson). `C_stats_only`/`C_stats_plus_elo` are expected to
    already be selected via logistic_tuning.py + persisted (see
    app/modelling/logistic_cli.py) — this function fits and evaluates,
    it doesn't grid-search."""
    elo_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "elo"))
    poisson_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson"))
    if elo_run is None or poisson_run is None:
        raise ModelsUnavailableError(
            "Run `python -m app.modelling.elo_cli` and `python -m app.modelling.poisson_cli` first."
        )

    matches = load_matches_with_team_stats(db)
    match_results = load_completed_matches(db)

    elo_config = EloConfig(**elo_run.config_json)
    elo_preds = elo_walk_forward(match_results, elo_config)
    elo_probs_by_match = {p.match_id: p.home_win_probability for p in elo_preds}

    poisson_config = PoissonConfig(**poisson_run.config_json)
    poisson_preds = poisson_walk_forward(match_results, poisson_config)
    poisson_probs_by_match = {p.match_id: p.home_win_probability for p in poisson_preds}

    rows = build_match_features(matches, elo_prob_by_match=elo_probs_by_match)
    tune_rows = [r for r in rows if r.season_year < EVALUATION_START_YEAR]
    eval_rows = [
        r for r in rows
        if EVALUATION_START_YEAR <= r.season_year and r.has_full_history and r.match_id in poisson_probs_by_match
    ]
    # Exclude the current, still-in-progress season from the evaluation set
    # — reuses evaluation.py's own current-year boundary (today's real
    # date) rather than a match-count heuristic, so it behaves correctly
    # regardless of how many matches a season happens to have.
    _warmup, complete_eval_rows, _current, _period = split_by_period(eval_rows, EVALUATION_START_YEAR)
    eval_rows = complete_eval_rows
    eval_rows.sort(key=lambda r: (r.scheduled_start, r.match_id))
    outcomes = [r.actual_home_outcome for r in eval_rows]
    match_ids = {r.match_id for r in eval_rows}

    elo_eval_probs = [elo_probs_by_match[r.match_id] for r in eval_rows]
    poisson_eval_probs = [poisson_probs_by_match[r.match_id] for r in eval_rows]
    elo_row = _score("elo", elo_eval_probs, outcomes)
    poisson_row = _score("poisson", poisson_eval_probs, outcomes)

    baseline_rows = []
    for name, baseline_fn in [
        ("baseline_always_home", always_home_baseline),
        ("baseline_historical_home_rate", historical_home_win_rate_baseline),
        ("baseline_simple_form", simple_form_baseline),
    ]:
        preds = baseline_fn(match_results)
        subset = [p for p in preds if p.match_id in match_ids]
        probs = [p.home_win_probability for p in subset]
        subset_outcomes = [p.actual_home_outcome for p in subset]
        baseline_rows.append(_score(name, probs, subset_outcomes))

    stats_only = _build_variant_report(
        "stats_only", STATS_FEATURE_NAMES, tune_rows, eval_rows, C_stats_only,
        elo_probs_by_match, elo_row.brier_score, elo_row.log_loss,
    )
    stats_plus_elo = _build_variant_report(
        "stats_plus_elo", STATS_PLUS_ELO_FEATURE_NAMES, tune_rows, eval_rows, C_stats_plus_elo,
        elo_probs_by_match, elo_row.brier_score, elo_row.log_loss,
    )

    return LogisticComparisonOverview(
        n_eval=len(eval_rows),
        evaluation_start_year=EVALUATION_START_YEAR,
        evaluation_end_year=max((r.season_year for r in eval_rows), default=EVALUATION_START_YEAR),
        baselines=baseline_rows,
        elo=elo_row,
        poisson=poisson_row,
        stats_only=stats_only,
        stats_plus_elo=stats_plus_elo,
    )
