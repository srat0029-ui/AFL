"""Fits the promoted disposal/goal model configuration on ALL available
historical data, for live projection of upcoming matches — deliberately
NOT the tune/eval split disposal_backtest.py/goal_backtest.py use for
honest evaluation. There is no held-out future to protect against here: an
upcoming match hasn't been played, so training on every completed match up
to today cannot leak into it — the more history the fit uses, the better.

WHICH model family/features/hyperparameters to fit is read from whichever
PlayerModelRun/GoalModelRun is currently `is_promoted=True` (model_name,
feature_names, config_json), not hardcoded — so re-running
`disposal_cli.py`/`goal_cli.py` and promoting a different model changes
what `project-upcoming` fits next time, automatically.

Distribution: every live disposal projection uses a Negative-Binomial
distribution (mean from the fitted model, alpha from the model's own
in-sample residuals via disposal_models.fit_residual_nb_alpha, or its
native NB dispersion where the family fits one directly) — even if the
currently-promoted research run's `distribution_method` happens to be
"empirical". The empirical-residual method needs a full stored quantile
array (see disposal_distribution.EmpiricalResidualDistribution) which
PlayerDisposalProjection does not persist; today's actual promoted
disposals config is itself "nb", so this is not a live behaviour change,
only a documented simplification for a hypothetical future promotion of an
"empirical" config.
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np

from app.models import GoalModelRun, PlayerModelRun
from app.player_modelling.disposal_baselines import BASELINES as DISPOSAL_BASELINES
from app.player_modelling.disposal_features import DisposalFeatureRow
from app.player_modelling.disposal_models import (
    BoostingRegressionConfig,
    fit_boosting_regression,
    fit_negative_binomial_regression,
    fit_poisson_regression,
    fit_residual_nb_alpha,
    fit_ridge,
)
from app.player_modelling.disposal_models import feature_matrix as disposal_feature_matrix
from app.player_modelling.disposal_models import target_vector as disposal_target_vector
from app.player_modelling.goal_baselines import BASELINES as GOAL_BASELINES
from app.player_modelling.goal_features import GoalFeatureRow
from app.player_modelling.goal_models import (
    BoostingGoalConfig,
    fit_hurdle_model,
)
from app.player_modelling.goal_models import fit_boosting_regression as fit_goal_boosting_regression
from app.player_modelling.goal_models import fit_negative_binomial_regression as fit_goal_negative_binomial_regression
from app.player_modelling.goal_models import fit_poisson_regression as fit_goal_poisson_regression
from app.player_modelling.goal_models import feature_matrix as goal_feature_matrix
from app.player_modelling.goal_models import target_vector as goal_target_vector


@dataclass(frozen=True)
class LiveDisposalModel:
    model_name: str  # the family, e.g. "ridge" — not the persisted "disposals_ridge"
    feature_names: tuple[str, ...]
    predict_many: Callable[[list[DisposalFeatureRow]], np.ndarray]
    nb_alpha: float


def fit_live_disposal_model(train_rows: list[DisposalFeatureRow], run: PlayerModelRun) -> LiveDisposalModel:
    family = run.model_name.removeprefix("disposals_")
    feature_names = tuple(run.feature_names)
    config = dict(run.config_json or {})

    if family in DISPOSAL_BASELINES:
        fn = DISPOSAL_BASELINES[family]

        def predict_many(rows: list[DisposalFeatureRow]) -> np.ndarray:
            return np.array([fn(r) or 0.0 for r in rows], dtype=float)

        predicted_all = predict_many(train_rows)
        alpha = fit_residual_nb_alpha(predicted_all, disposal_target_vector(train_rows))
        return LiveDisposalModel(model_name=family, feature_names=feature_names, predict_many=predict_many, nb_alpha=alpha)

    if family == "ridge":
        fitted = fit_ridge(train_rows, feature_names, alpha=config.get("alpha", 5.0))
    elif family == "poisson_regression":
        fitted = fit_poisson_regression(train_rows, feature_names)
    elif family == "negative_binomial":
        fitted = fit_negative_binomial_regression(train_rows, feature_names)
    elif family in ("gbm_xgboost", "gbm_lightgbm"):
        fitted = fit_boosting_regression(train_rows, feature_names, BoostingRegressionConfig(**config))
    else:
        raise ValueError(f"unsupported disposal model family for live projection: {family!r}")

    def predict_many(rows: list[DisposalFeatureRow]) -> np.ndarray:
        return fitted.predict_fn(disposal_feature_matrix(rows, feature_names))

    predicted_all = predict_many(train_rows)
    alpha = fitted.nb_alpha if fitted.nb_alpha is not None else fit_residual_nb_alpha(predicted_all, disposal_target_vector(train_rows))
    return LiveDisposalModel(model_name=family, feature_names=feature_names, predict_many=predict_many, nb_alpha=alpha)


@dataclass(frozen=True)
class LiveGoalModel:
    model_name: str
    feature_names: tuple[str, ...]
    distribution_kind: str  # "nb" | "hurdle"
    # For "nb": predict_mean returns mu, nb_alpha is fixed. For "hurdle":
    # predict_mean returns the implied mean (p_score-weighted), and
    # predict_hurdle_params returns (p_score, mu_scored) per row.
    predict_mean: Callable[[list[GoalFeatureRow]], np.ndarray]
    nb_alpha: float | None
    predict_hurdle_params: Callable[[list[GoalFeatureRow]], tuple[np.ndarray, np.ndarray]] | None
    alpha_scored: float | None


def fit_live_goal_model(train_rows: list[GoalFeatureRow], run: GoalModelRun) -> LiveGoalModel:
    family = run.model_name.removeprefix("goals_")
    feature_names = tuple(run.feature_names)
    config = dict(run.config_json or {})

    if family in GOAL_BASELINES:
        fn = GOAL_BASELINES[family]

        def predict_mean(rows: list[GoalFeatureRow]) -> np.ndarray:
            return np.array([fn(r) or 0.0 for r in rows], dtype=float)

        predicted_all = predict_mean(train_rows)
        alpha = fit_residual_nb_alpha(predicted_all, goal_target_vector(train_rows))
        return LiveGoalModel(
            model_name=family, feature_names=feature_names, distribution_kind="nb",
            predict_mean=predict_mean, nb_alpha=alpha, predict_hurdle_params=None, alpha_scored=None,
        )

    if family == "hurdle":
        from app.player_modelling.goal_distribution import HurdleDistribution

        fitted = fit_hurdle_model(train_rows, feature_names)

        def predict_hurdle_params(rows: list[GoalFeatureRow]) -> tuple[np.ndarray, np.ndarray]:
            X = goal_feature_matrix(rows, feature_names)
            return fitted.predict_p_score(X), fitted.predict_mu_scored(X)

        def predict_mean(rows: list[GoalFeatureRow]) -> np.ndarray:
            p_score, mu_scored = predict_hurdle_params(rows)
            return np.array(
                [HurdleDistribution(p_score=float(p), mu_scored=float(mu), alpha_scored=fitted.alpha_scored).mean() for p, mu in zip(p_score, mu_scored)]
            )

        return LiveGoalModel(
            model_name=family, feature_names=feature_names, distribution_kind="hurdle",
            predict_mean=predict_mean, nb_alpha=None, predict_hurdle_params=predict_hurdle_params,
            alpha_scored=fitted.alpha_scored,
        )

    if family == "poisson_regression":
        fitted = fit_goal_poisson_regression(train_rows, feature_names)
    elif family == "negative_binomial":
        fitted = fit_goal_negative_binomial_regression(train_rows, feature_names)
    elif family in ("gbm_xgboost", "gbm_lightgbm"):
        fitted = fit_goal_boosting_regression(train_rows, feature_names, BoostingGoalConfig(**config))
    else:
        raise ValueError(f"unsupported goal model family for live projection: {family!r}")

    def predict_mean(rows: list[GoalFeatureRow]) -> np.ndarray:
        return fitted.predict_fn(goal_feature_matrix(rows, feature_names))

    predicted_all = predict_mean(train_rows)
    alpha = fitted.nb_alpha if fitted.nb_alpha is not None else fit_residual_nb_alpha(predicted_all, goal_target_vector(train_rows))
    return LiveGoalModel(
        model_name=family, feature_names=feature_names, distribution_kind="nb",
        predict_mean=predict_mean, nb_alpha=alpha, predict_hurdle_params=None, alpha_scored=None,
    )
