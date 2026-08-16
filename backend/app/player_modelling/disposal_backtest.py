"""Chronological backtesting for the disposals model — Sections 10-11 of the
disposal-prediction stage brief.

Split: matches before EVALUATION_START_YEAR (2019) are "warm-up/tune" —
used to fit model parameters and to build up the rolling player/team
history the feature builder needs — and matches from EVALUATION_START_YEAR
onward are "evaluation." This is the EXACT same split already used
league-wide for the team models (see app/backtesting/evaluation.py's
EVALUATION_START_YEAR docstring) and for the same reason: three seasons is
enough rolling history for the point-in-time features to be meaningful
(most players have several prior games by their first 2019 appearance),
and reusing an established split keeps this model comparable to the rest
of the project's validation story rather than inventing a new convention.

Models are fit ONCE on the tune-period rows and used unchanged across the
whole evaluation period - not re-fit per row. This matches
app/backtesting/boosting_report.py's exact pattern for the team-level
boosting model and is standard walk-forward-evaluation practice: since
every feature is already point-in-time-correct by construction (see
disposal_features.py), a fixed model evaluated on strictly-later data
introduces no leakage - the leakage risk from "fit once, evaluate later"
would only be a fitting-time concern (a model that peeked at eval-period
data while being fit), which this design structurally avoids.

Common evaluation set (Section 11): every model and baseline is scored on
the exact same eval_rows list - not a per-model subset - so comparisons
are apples-to-apples. See build_dataset()'s docstring for what "eligible"
and "excluded" mean here.
"""

from dataclasses import dataclass, field

import numpy as np
from sqlalchemy.orm import Session

from app.player_modelling.disposal_baselines import BASELINES
from app.player_modelling.disposal_data import load_player_game_rows, load_team_game_rows
from app.player_modelling.disposal_distribution import EmpiricalResidualDistribution, NegativeBinomialDistribution
from app.player_modelling.disposal_features import PLAYER_FEATURE_NAMES, DisposalFeatureBuilder, DisposalFeatureRow
from app.player_modelling.disposal_models import (
    BoostingRegressionConfig,
    FittedDisposalModel,
    feature_matrix,
    fit_boosting_regression,
    fit_negative_binomial_regression,
    fit_poisson_regression,
    fit_residual_nb_alpha,
    fit_ridge,
    target_vector,
)
from app.player_modelling.disposal_team_context import build_team_context

EVALUATION_START_YEAR = 2019


@dataclass(frozen=True)
class DatasetSplit:
    all_rows: list[DisposalFeatureRow]
    tune_rows: list[DisposalFeatureRow]
    eval_rows: list[DisposalFeatureRow]


def build_dataset(db: Session, season_scale_factors: dict[int, float] | None = None) -> DatasetSplit:
    """Eligible = every (player, completed match) row where that player has
    a recorded disposal count (see disposal_data.py's docstring for why
    this is the eligibility rule this stage uses - "given selected and
    played", not team-selection prediction). No rows are excluded here: the
    prior-stage player-data audit confirmed every PlayerMatchStat row has
    disposals populated (0% missing), so eligible == loaded == 91,780+ rows
    with nothing filtered out for missingness. The tune/eval split below is
    a MODELLING boundary, not an exclusion - tune rows remain part of
    'all_rows' and are used to build history feeding into eval-row
    features.

    season_scale_factors: passed straight through to DisposalFeatureBuilder
    (see its docstring) - None (the default) means no adjustment. Used by
    disposal_analysis.compare_2020_handling() to build a second dataset
    with 2020 history rescaled, without duplicating the DB load."""
    player_rows = load_player_game_rows(db)
    team_rows = load_team_game_rows(db)
    team_context = build_team_context(db)
    return build_dataset_from_rows(player_rows, team_rows, team_context, season_scale_factors)


def build_dataset_from_rows(
    player_rows, team_rows, team_context: dict[int, dict[int, dict]], season_scale_factors: dict[int, float] | None = None
) -> DatasetSplit:
    """The feature-building half of build_dataset(), split out so callers
    that already have the raw rows loaded (e.g. comparing two
    season_scale_factors variants) don't need to hit the DB again."""
    builder = DisposalFeatureBuilder(team_context=team_context, season_scale_factors=season_scale_factors)
    feature_rows = builder.build(player_rows, team_rows)

    tune_rows = [r for r in feature_rows if r.season_year < EVALUATION_START_YEAR]
    eval_rows = [r for r in feature_rows if r.season_year >= EVALUATION_START_YEAR]
    return DatasetSplit(all_rows=feature_rows, tune_rows=tune_rows, eval_rows=eval_rows)


@dataclass(frozen=True)
class PredictionRecord:
    """One model's prediction for one eval row - enough to compute every
    metric the brief asks for (point, probability, interval) plus enough
    identifying context to slice by season/player-history/TOG later."""

    player_id: int
    match_id: int
    team_id: int
    season_year: int
    is_final: bool
    games_of_history: int
    tog_last5_avg: float | None
    disposals_last5_std: float | None
    actual: int
    predicted_mean: float
    nb_alpha: float
    # A shared numpy array reference (see disposal_distribution.py's
    # EmpiricalResidualDistribution docstring for why it's an array, not a
    # tuple, and why "shared" matters) - every PredictionRecord from the
    # same model fit points at the SAME array object, not a per-row copy.
    empirical_residuals: np.ndarray = field(repr=False)

    def nb_distribution(self) -> NegativeBinomialDistribution:
        return NegativeBinomialDistribution(mu=self.predicted_mean, alpha=self.nb_alpha)

    def empirical_distribution(self) -> EmpiricalResidualDistribution:
        return EmpiricalResidualDistribution(mu=self.predicted_mean, sorted_residuals=self.empirical_residuals)


def _predict_and_wrap(
    model_name: str,
    predicted: np.ndarray,
    eval_rows: list[DisposalFeatureRow],
    nb_alpha: float,
    empirical_residuals: np.ndarray,
) -> list[PredictionRecord]:
    return [
        PredictionRecord(
            player_id=r.player_id,
            match_id=r.match_id,
            team_id=r.team_id,
            season_year=r.season_year,
            is_final=r.is_final,
            games_of_history=r.games_of_history,
            tog_last5_avg=r.features.get("tog_last5_avg"),
            disposals_last5_std=r.features.get("disposals_last5_std"),
            actual=r.disposals,
            predicted_mean=float(p),
            nb_alpha=nb_alpha,
            empirical_residuals=empirical_residuals,
        )
        for r, p in zip(eval_rows, predicted)
    ]


def run_baselines(split: DatasetSplit) -> dict[str, list[PredictionRecord]]:
    """Baselines need no fitting - each is a pure function of a row's own
    features (see disposal_baselines.py). Their NB alpha/empirical
    residuals are still derived honestly from TUNE-period performance only,
    so a baseline's distribution is just as leakage-safe as a fitted
    model's."""
    results = {}
    for name, fn in BASELINES.items():
        tune_pred = np.array([fn(r) or 0.0 for r in split.tune_rows])
        tune_actual = target_vector(split.tune_rows)
        alpha = fit_residual_nb_alpha(tune_pred, tune_actual)
        residuals = np.sort(tune_actual - tune_pred)

        eval_pred = np.array([fn(r) or 0.0 for r in split.eval_rows])
        results[name] = _predict_and_wrap(name, eval_pred, split.eval_rows, alpha, residuals)
    return results


DEFAULT_MODEL_NAMES = ("ridge", "poisson_regression", "negative_binomial", "gbm_xgboost", "gbm_lightgbm")


def run_candidate_models(
    split: DatasetSplit, feature_names: tuple[str, ...] = PLAYER_FEATURE_NAMES, model_names: tuple[str, ...] = DEFAULT_MODEL_NAMES
) -> dict[str, list[PredictionRecord]]:
    """Fits every requested candidate model once on split.tune_rows,
    predicts on split.eval_rows - see module docstring for why fitting
    once (not per-row) is the correct, leakage-safe design here."""
    results = {}
    tune_actual = target_vector(split.tune_rows)
    X_eval = feature_matrix(split.eval_rows, feature_names)

    fitted: dict[str, FittedDisposalModel] = {}
    if "ridge" in model_names:
        fitted["ridge"] = fit_ridge(split.tune_rows, feature_names)
    if "poisson_regression" in model_names:
        fitted["poisson_regression"] = fit_poisson_regression(split.tune_rows, feature_names)
    if "negative_binomial" in model_names:
        fitted["negative_binomial"] = fit_negative_binomial_regression(split.tune_rows, feature_names)
    if "gbm_xgboost" in model_names:
        fitted["gbm_xgboost"] = fit_boosting_regression(split.tune_rows, feature_names, BoostingRegressionConfig(library="xgboost"))
    if "gbm_lightgbm" in model_names:
        fitted["gbm_lightgbm"] = fit_boosting_regression(split.tune_rows, feature_names, BoostingRegressionConfig(library="lightgbm"))

    X_tune = feature_matrix(split.tune_rows, feature_names)
    for name, model in fitted.items():
        tune_pred = model.predict_fn(X_tune)
        alpha = model.nb_alpha if model.nb_alpha is not None else fit_residual_nb_alpha(tune_pred, tune_actual)
        residuals = np.sort(tune_actual - tune_pred)

        eval_pred = model.predict_fn(X_eval)
        results[name] = _predict_and_wrap(name, eval_pred, split.eval_rows, alpha, residuals)
    return results
