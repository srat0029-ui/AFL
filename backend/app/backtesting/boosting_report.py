"""Ties together the gradient-boosting evaluation: comparing XGBoost and
LightGBM across five controlled feature sets (A-E, per the Stage brief),
picking the single best (library, feature set) candidate, then running
calibration, bootstrap uncertainty, feature importance, interaction
analysis, season stability, disagreement vs Elo, the promotion decision,
and a simple Elo+boosting ensemble experiment on that candidate — always
over the identical evaluation match set used by every other model report
in this app.

Hyperparameters are tuned once per library (on the richest feature set, D:
all stats + Elo — see boosting_tuning.py) and then reused unchanged across
all five feature sets, so the A-E comparison isolates the effect of the
*features*, not a moving target of re-tuned hyperparameters per set.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtesting.evaluation import EVALUATION_START_YEAR, split_by_period
from app.backtesting.logistic_comparison import LogisticComparisonReport, build_logistic_vs_elo_comparison
from app.backtesting.segments import BacktestSegment, win_prob_metrics_from_pairs
from app.modelling.ablation import permutation_importance
from app.modelling.boosting import BoostingConfig, BoostingPrediction, fit_boosting_model, predict
from app.modelling.boosting_interactions import InteractionFinding, analyze_interactions, shap_values, supports_interactions
from app.modelling.boosting_tuning import select_best_boosting_config
from app.modelling.bootstrap import BootstrapResult, bootstrap_metric_difference
from app.modelling.calibration_methods import select_calibration_method
from app.modelling.data_loading import load_completed_matches, load_matches_with_team_stats
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.ensemble import select_ensemble_weight
from app.modelling.features import ELO_FEATURE_NAME, FEATURE_GROUPS, STATS_FEATURE_NAMES, STATS_PLUS_ELO_FEATURE_NAMES, MatchFeatureRow, build_match_features
from app.modelling.metrics import brier_score, expected_calibration_error, favourite_calibration_table, log_loss
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import PoissonConfig
from app.modelling.promotion import PromotionDecision, evaluate_promotion_rule
from app.models import ModelRun

INNER_VALIDATION_START_YEAR = 2018
LIBRARIES = ("xgboost", "lightgbm")

FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "A_elo_only": (ELO_FEATURE_NAME,),
    "B_form_scoring_elo": tuple(FEATURE_GROUPS["recent_form"] + FEATURE_GROUPS["recent_scoring"] + [ELO_FEATURE_NAME]),
    "C_inside50_form_scoring_elo": tuple(
        FEATURE_GROUPS["recent_form"] + FEATURE_GROUPS["recent_scoring"] + FEATURE_GROUPS["inside_50s"] + [ELO_FEATURE_NAME]
    ),
    "D_all_stats_elo": tuple(STATS_PLUS_ELO_FEATURE_NAMES),
    "E_all_stats_no_elo": tuple(STATS_FEATURE_NAMES),
}
# Feature set used for hyperparameter tuning (the richest one) — see module docstring.
TUNING_FEATURE_SET = "D_all_stats_elo"


class ModelsUnavailableError(Exception):
    """Raised when elo_cli.py / poisson_cli.py haven't been run yet."""


@dataclass(frozen=True)
class BaselineRow:
    name: str
    n: int
    brier_score: float
    log_loss: float
    accuracy: float


@dataclass(frozen=True)
class FeatureSetCandidateResult:
    label: str
    library: str
    feature_names: tuple[str, ...]
    n_eval: int
    brier_score: float
    log_loss: float
    accuracy: float


@dataclass(frozen=True)
class BoostingAblationResult:
    label: str
    feature_names: tuple[str, ...]
    n_eval: int
    brier_score: float
    log_loss: float
    brier_vs_elo_alone: float | None


@dataclass(frozen=True)
class BoostingBestCandidateReport:
    label: str
    library: str
    feature_names: tuple[str, ...]
    hyperparameters: dict
    calibration_method: str
    n_eval: int
    brier_score: float
    log_loss: float
    accuracy: float
    calibration: list[dict]
    calibration_ece: float | None
    permutation_importance: dict[str, float]
    shap_importance: dict[str, float] | None
    feature_group_ablation: list[BoostingAblationResult]
    bootstrap_vs_elo: BootstrapResult
    by_season: list[BacktestSegment]
    disagreement_vs_elo: LogisticComparisonReport
    interactions: list[InteractionFinding]
    promotion: PromotionDecision


@dataclass(frozen=True)
class EnsembleReport:
    boosting_weight: float
    elo: BaselineRow
    boosting: BaselineRow
    ensemble: BaselineRow
    bootstrap_ensemble_vs_elo: BootstrapResult
    bootstrap_ensemble_vs_boosting: BootstrapResult
    use_ensemble: bool


@dataclass(frozen=True)
class BoostingComparisonOverview:
    n_eval: int
    evaluation_start_year: int
    evaluation_end_year: int
    feature_set_candidates: list[FeatureSetCandidateResult]
    best: BoostingBestCandidateReport
    ensemble: EnsembleReport


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


def _fit_and_score_boosting(tune_rows, eval_rows, feature_names, config_template: BoostingConfig) -> tuple[float, float, list[BoostingPrediction]]:
    config = BoostingConfig(
        library=config_template.library, feature_names=feature_names,
        max_depth=config_template.max_depth, learning_rate=config_template.learning_rate,
        n_estimators=config_template.n_estimators, min_child_weight=config_template.min_child_weight,
        subsample=config_template.subsample, colsample_bytree=config_template.colsample_bytree,
        reg_alpha=config_template.reg_alpha, reg_lambda=config_template.reg_lambda,
        random_state=config_template.random_state,
    )
    full_tune = [r for r in tune_rows if r.has_full_history]
    model = fit_boosting_model(full_tune, config)
    preds = predict(model, eval_rows, feature_names)
    probs = [p.home_win_probability for p in preds]
    outcomes = [p.actual_home_outcome for p in preds]
    return brier_score(probs, outcomes), log_loss(probs, outcomes), preds


def _feature_group_ablation_for_boosting(
    tune_rows, eval_rows, best_config: BoostingConfig, elo_alone_brier: float
) -> list[BoostingAblationResult]:
    results = []
    elo_only = (ELO_FEATURE_NAME,)
    elo_brier, elo_logloss, _ = _fit_and_score_boosting(tune_rows, eval_rows, elo_only, best_config)
    baseline = elo_alone_brier
    results.append(BoostingAblationResult("elo_only", elo_only, len(eval_rows), elo_brier, elo_logloss, elo_brier - baseline))

    for group_name, group_features in FEATURE_GROUPS.items():
        names = (ELO_FEATURE_NAME, *group_features)
        b, ll, _ = _fit_and_score_boosting(tune_rows, eval_rows, names, best_config)
        results.append(BoostingAblationResult(f"elo_plus_{group_name}", names, len(eval_rows), b, ll, b - baseline))

    all_b, all_ll, _ = _fit_and_score_boosting(tune_rows, eval_rows, tuple(STATS_PLUS_ELO_FEATURE_NAMES), best_config)
    results.append(BoostingAblationResult("elo_plus_all_stats", tuple(STATS_PLUS_ELO_FEATURE_NAMES), len(eval_rows), all_b, all_ll, all_b - baseline))
    return results


def _shap_importance(model, rows, feature_names) -> dict[str, float] | None:
    import numpy as np

    from app.modelling.logistic import feature_matrix

    if not supports_interactions(model) or not rows:
        return None
    X = feature_matrix(rows, feature_names)
    values = shap_values(model, X)
    if values is None:
        return None
    return {name: float(np.mean(np.abs(values[:, i]))) for i, name in enumerate(feature_names)}


def build_boosting_comparison(db: Session) -> BoostingComparisonOverview:
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
    eval_candidates = [
        r for r in rows if EVALUATION_START_YEAR <= r.season_year and r.has_full_history and r.match_id in poisson_probs_by_match
    ]
    _warmup, eval_rows, _current, _period = split_by_period(eval_candidates, EVALUATION_START_YEAR)
    eval_rows.sort(key=lambda r: (r.scheduled_start, r.match_id))
    outcomes = [r.actual_home_outcome for r in eval_rows]

    elo_eval_probs = [elo_probs_by_match[r.match_id] for r in eval_rows]
    elo_row = _score("elo", elo_eval_probs, outcomes)

    # --- tune each library once, on the richest feature set ---
    tuned_configs: dict[str, BoostingConfig] = {}
    for library in LIBRARIES:
        config, _leaderboard = select_best_boosting_config(
            library, tune_rows, FEATURE_SETS[TUNING_FEATURE_SET], INNER_VALIDATION_START_YEAR
        )
        tuned_configs[library] = config

    # --- evaluate every (library, feature set) combination on the identical eval set ---
    candidates: list[FeatureSetCandidateResult] = []
    candidate_preds: dict[tuple[str, str], list[BoostingPrediction]] = {}
    for library in LIBRARIES:
        for label, feature_names in FEATURE_SETS.items():
            b, ll, preds = _fit_and_score_boosting(tune_rows, eval_rows, feature_names, tuned_configs[library])
            acc = win_prob_metrics_from_pairs([p.home_win_probability for p in preds], outcomes).get("accuracy", float("nan"))
            candidates.append(FeatureSetCandidateResult(label, library, feature_names, len(eval_rows), b, ll, acc))
            candidate_preds[(library, label)] = preds

    best_candidate = min(candidates, key=lambda c: c.brier_score)
    best_config = tuned_configs[best_candidate.library]
    best_feature_names = best_candidate.feature_names

    # --- full analysis of the best candidate ---
    final_config = BoostingConfig(
        library=best_config.library, feature_names=best_feature_names,
        max_depth=best_config.max_depth, learning_rate=best_config.learning_rate, n_estimators=best_config.n_estimators,
        min_child_weight=best_config.min_child_weight, subsample=best_config.subsample, colsample_bytree=best_config.colsample_bytree,
        reg_alpha=best_config.reg_alpha, reg_lambda=best_config.reg_lambda, random_state=best_config.random_state,
    )
    full_tune = [r for r in tune_rows if r.has_full_history]
    final_model = fit_boosting_model(full_tune, final_config)
    final_preds = predict(final_model, eval_rows, best_feature_names)
    raw_probs = [p.home_win_probability for p in final_preds]

    inner_train = [r for r in tune_rows if r.season_year < INNER_VALIDATION_START_YEAR and r.has_full_history]
    inner_val = [r for r in tune_rows if r.season_year >= INNER_VALIDATION_START_YEAR and r.has_full_history]
    inner_model = fit_boosting_model(inner_train, final_config)
    inner_preds = predict(inner_model, inner_val, best_feature_names)
    calibrator, _calib_scores = select_calibration_method(
        [p.home_win_probability for p in inner_preds], [p.actual_home_outcome for p in inner_preds]
    )
    calibrated_probs = calibrator.apply(raw_probs)
    calibration_table = favourite_calibration_table(calibrated_probs, outcomes)

    bootstrap = bootstrap_metric_difference(elo_eval_probs, calibrated_probs, outcomes, brier_score)

    season_years = sorted({r.season_year for r in eval_rows})
    season_improvements = []
    for year in season_years:
        idx = [i for i, r in enumerate(eval_rows) if r.season_year == year]
        season_elo = brier_score([elo_eval_probs[i] for i in idx], [outcomes[i] for i in idx])
        season_candidate = brier_score([calibrated_probs[i] for i in idx], [outcomes[i] for i in idx])
        season_improvements.append(season_candidate < season_elo)

    promotion = evaluate_promotion_rule(
        candidate_brier=brier_score(calibrated_probs, outcomes),
        elo_brier=elo_row.brier_score,
        candidate_log_loss=log_loss(calibrated_probs, outcomes),
        elo_log_loss=elo_row.log_loss,
        candidate_ece=expected_calibration_error(calibration_table),
        brier_improvement_bootstrap=bootstrap,
        season_brier_improvements=season_improvements,
    )

    best_report = BoostingBestCandidateReport(
        label=best_candidate.label,
        library=best_candidate.library,
        feature_names=best_feature_names,
        hyperparameters={
            "max_depth": final_config.max_depth, "learning_rate": final_config.learning_rate,
            "n_estimators": final_config.n_estimators, "min_child_weight": final_config.min_child_weight,
            "subsample": final_config.subsample, "colsample_bytree": final_config.colsample_bytree,
            "reg_alpha": final_config.reg_alpha, "reg_lambda": final_config.reg_lambda,
        },
        calibration_method=calibrator.method,
        n_eval=len(eval_rows),
        brier_score=brier_score(calibrated_probs, outcomes),
        log_loss=log_loss(calibrated_probs, outcomes),
        accuracy=win_prob_metrics_from_pairs(calibrated_probs, outcomes).get("accuracy", float("nan")),
        calibration=calibration_table,
        calibration_ece=expected_calibration_error(calibration_table),
        permutation_importance=permutation_importance(final_model, eval_rows, best_feature_names),
        shap_importance=_shap_importance(final_model, eval_rows, best_feature_names),
        feature_group_ablation=_feature_group_ablation_for_boosting(tune_rows, eval_rows, final_config, elo_row.brier_score),
        bootstrap_vs_elo=bootstrap,
        by_season=_by_season(eval_rows, calibrated_probs),
        disagreement_vs_elo=build_logistic_vs_elo_comparison(elo_probs_by_match, final_preds),
        interactions=analyze_interactions(final_model, eval_rows, best_feature_names),
        promotion=promotion,
    )

    # --- ensemble experiment ---
    inner_elo_probs = [elo_probs_by_match[r.match_id] for r in inner_val]
    inner_boosting_probs = [p.home_win_probability for p in inner_preds]
    inner_outcomes = [r.actual_home_outcome for r in inner_val]
    boosting_weight, _weight_leaderboard = select_ensemble_weight(inner_elo_probs, inner_boosting_probs, inner_outcomes)

    from app.modelling.ensemble import blend

    ensemble_probs = blend(elo_eval_probs, calibrated_probs, boosting_weight)
    ensemble_row = _score("ensemble", ensemble_probs, outcomes)
    boosting_row = _score("boosting", calibrated_probs, outcomes)

    bootstrap_ensemble_vs_elo = bootstrap_metric_difference(elo_eval_probs, ensemble_probs, outcomes, brier_score)
    bootstrap_ensemble_vs_boosting = bootstrap_metric_difference(calibrated_probs, ensemble_probs, outcomes, brier_score)
    use_ensemble = (
        ensemble_row.brier_score < elo_row.brier_score
        and ensemble_row.brier_score < boosting_row.brier_score
        and bootstrap_ensemble_vs_elo.excludes_zero
    )

    ensemble_report = EnsembleReport(
        boosting_weight=boosting_weight,
        elo=elo_row,
        boosting=boosting_row,
        ensemble=ensemble_row,
        bootstrap_ensemble_vs_elo=bootstrap_ensemble_vs_elo,
        bootstrap_ensemble_vs_boosting=bootstrap_ensemble_vs_boosting,
        use_ensemble=use_ensemble,
    )

    return BoostingComparisonOverview(
        n_eval=len(eval_rows),
        evaluation_start_year=EVALUATION_START_YEAR,
        evaluation_end_year=max((r.season_year for r in eval_rows), default=EVALUATION_START_YEAR),
        feature_set_candidates=candidates,
        best=best_report,
        ensemble=ensemble_report,
    )
