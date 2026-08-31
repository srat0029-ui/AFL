"""Chronological backtesting for the goal model — same split/common-eval-
set/fit-once discipline as disposal_backtest.py (see its module docstring
for the full rationale, which applies unchanged here): tune 2016-2018,
eval 2019-2025, every model fit once on tune rows only.

The one real structural difference from disposals: a fitted model here may
produce EITHER a single-process NB distribution (mu, alpha) OR a two-part
hurdle distribution (p_score, mu_scored, alpha_scored) - see
goal_distribution.py. GoalPredictionRecord carries whichever fields its
model actually populated; unused fields stay None.
"""

from dataclasses import dataclass

import numpy as np
from sqlalchemy.orm import Session

from app.player_modelling.disposal_models import fit_residual_nb_alpha
from app.player_modelling.goal_baselines import BASELINES
from app.player_modelling.goal_data import load_player_goal_game_rows, load_team_goal_game_rows
from app.player_modelling.goal_distribution import HurdleDistribution, NegativeBinomialGoalDistribution
from app.player_modelling.goal_features import PLAYER_FEATURE_NAMES, GoalFeatureBuilder, GoalFeatureRow
from app.player_modelling.goal_models import (
    BoostingGoalConfig,
    feature_matrix,
    fit_boosting_regression,
    fit_hurdle_model,
    fit_negative_binomial_regression,
    fit_poisson_regression,
    target_vector,
)
from app.player_modelling.disposal_team_context import build_team_context

EVALUATION_START_YEAR = 2019


@dataclass(frozen=True)
class GoalDatasetSplit:
    all_rows: list[GoalFeatureRow]
    tune_rows: list[GoalFeatureRow]
    eval_rows: list[GoalFeatureRow]


def build_goal_dataset(db: Session, season_scale_factors: dict[int, float] | None = None) -> GoalDatasetSplit:
    """Eligible = every (player, completed match) row with a recorded goal
    count (the goal audit found 0% missing, same as disposals). No rows
    excluded."""
    player_rows = load_player_goal_game_rows(db)
    team_rows = load_team_goal_game_rows(db)
    team_context = build_team_context(db)
    return build_goal_dataset_from_rows(player_rows, team_rows, team_context, season_scale_factors)


def build_goal_dataset_from_rows(player_rows, team_rows, team_context, season_scale_factors=None) -> GoalDatasetSplit:
    builder = GoalFeatureBuilder(team_context=team_context, season_scale_factors=season_scale_factors)
    feature_rows = builder.build(player_rows, team_rows)
    tune_rows = [r for r in feature_rows if r.season_year < EVALUATION_START_YEAR]
    eval_rows = [r for r in feature_rows if r.season_year >= EVALUATION_START_YEAR]
    return GoalDatasetSplit(all_rows=feature_rows, tune_rows=tune_rows, eval_rows=eval_rows)


@dataclass(frozen=True)
class GoalPredictionRecord:
    player_id: int
    match_id: int
    team_id: int
    season_year: int
    is_final: bool
    games_of_history: int
    tog_last5_avg: float | None
    zero_goal_rate_last10: float | None
    actual: int
    predicted_mean: float
    distribution_kind: str  # "nb" | "hurdle"
    nb_alpha: float | None = None
    p_score: float | None = None
    mu_scored: float | None = None
    alpha_scored: float | None = None

    def distribution(self):
        if self.distribution_kind == "hurdle":
            return HurdleDistribution(p_score=self.p_score, mu_scored=self.mu_scored, alpha_scored=self.alpha_scored)
        return NegativeBinomialGoalDistribution(mu=self.predicted_mean, alpha=self.nb_alpha)


def _wrap(
    eval_rows: list[GoalFeatureRow],
    predicted_mean: np.ndarray,
    distribution_kind: str,
    nb_alpha: float | None = None,
    p_score: np.ndarray | None = None,
    mu_scored: np.ndarray | None = None,
    alpha_scored: float | None = None,
) -> list[GoalPredictionRecord]:
    records = []
    for i, r in enumerate(eval_rows):
        records.append(
            GoalPredictionRecord(
                player_id=r.player_id,
                match_id=r.match_id,
                team_id=r.team_id,
                season_year=r.season_year,
                is_final=r.is_final,
                games_of_history=r.games_of_history,
                tog_last5_avg=r.features.get("tog_last5_avg"),
                zero_goal_rate_last10=r.features.get("zero_goal_rate_last10"),
                actual=r.goals,
                predicted_mean=float(predicted_mean[i]),
                distribution_kind=distribution_kind,
                nb_alpha=nb_alpha,
                p_score=float(p_score[i]) if p_score is not None else None,
                mu_scored=float(mu_scored[i]) if mu_scored is not None else None,
                alpha_scored=alpha_scored,
            )
        )
    return records


def run_goal_baselines(split: GoalDatasetSplit) -> dict[str, list[GoalPredictionRecord]]:
    results = {}
    for name, fn in BASELINES.items():
        tune_pred = np.array([fn(r) or 0.0 for r in split.tune_rows])
        tune_actual = target_vector(split.tune_rows)
        alpha = fit_residual_nb_alpha(tune_pred, tune_actual)
        eval_pred = np.array([fn(r) or 0.0 for r in split.eval_rows])
        results[name] = _wrap(split.eval_rows, eval_pred, "nb", nb_alpha=alpha)
    return results


DEFAULT_GOAL_MODEL_NAMES = ("poisson_regression", "negative_binomial", "hurdle", "gbm_xgboost", "gbm_lightgbm")


def run_goal_candidate_models(
    split: GoalDatasetSplit,
    feature_names: tuple[str, ...] = PLAYER_FEATURE_NAMES,
    model_names: tuple[str, ...] = DEFAULT_GOAL_MODEL_NAMES,
) -> dict[str, list[GoalPredictionRecord]]:
    results = {}
    tune_actual = target_vector(split.tune_rows)
    X_eval = feature_matrix(split.eval_rows, feature_names)
    X_tune = feature_matrix(split.tune_rows, feature_names)

    if "poisson_regression" in model_names:
        m = fit_poisson_regression(split.tune_rows, feature_names)
        tune_pred = m.predict_fn(X_tune)
        alpha = fit_residual_nb_alpha(tune_pred, tune_actual)
        results["poisson_regression"] = _wrap(split.eval_rows, m.predict_fn(X_eval), "nb", nb_alpha=alpha)

    if "negative_binomial" in model_names:
        m = fit_negative_binomial_regression(split.tune_rows, feature_names)
        results["negative_binomial"] = _wrap(split.eval_rows, m.predict_fn(X_eval), "nb", nb_alpha=m.nb_alpha)

    if "hurdle" in model_names:
        h = fit_hurdle_model(split.tune_rows, feature_names)
        p_score_eval = h.predict_p_score(X_eval)
        mu_scored_eval = h.predict_mu_scored(X_eval)
        # mean of the hurdle distribution = p_score * mu_scored / (1 - P(0; mu_scored)) truncation-adjusted mean;
        # approximated here by p_score * mu_scored (a slight understatement, since the truncated mean is a bit
        # higher than mu_scored - acceptable for the point-estimate/MAE report; the full distribution used for
        # probabilities is always exact, computed directly by HurdleDistribution).
        predicted_mean = p_score_eval * mu_scored_eval
        results["hurdle"] = _wrap(
            split.eval_rows, predicted_mean, "hurdle", p_score=p_score_eval, mu_scored=mu_scored_eval, alpha_scored=h.alpha_scored
        )

    for library in ("xgboost", "lightgbm"):
        name = f"gbm_{library}"
        if name in model_names:
            m = fit_boosting_regression(split.tune_rows, feature_names, BoostingGoalConfig(library=library))
            tune_pred = m.predict_fn(X_tune)
            alpha = fit_residual_nb_alpha(tune_pred, tune_actual)
            results[name] = _wrap(split.eval_rows, m.predict_fn(X_eval), "nb", nb_alpha=alpha)

    return results
