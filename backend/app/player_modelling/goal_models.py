"""Candidate point-prediction / distribution-parameter models for goals —
Sections 6-7 of the goal-prediction stage brief. Goals are a low-count,
zero-heavy target (see the real audit: mean~0.53, 66.8% zero games,
variance/mean~1.66) so, unlike disposals, Ridge is deliberately NOT the
default here - Poisson/NB regression and a two-part hurdle model are the
natural fits for this target shape (see goal_distribution.py's module
docstring for why hurdle over zero-inflated mixtures).
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np
from sklearn.linear_model import LogisticRegression

from app.player_modelling.disposal_models import fit_residual_nb_alpha
from app.player_modelling.goal_features import GoalFeatureRow

RANDOM_STATE = 42


def feature_matrix(rows: list[GoalFeatureRow], feature_names: tuple[str, ...]) -> np.ndarray:
    return np.array(
        [[row.features.get(name) if row.features.get(name) is not None else np.nan for name in feature_names] for row in rows],
        dtype=float,
    )


def target_vector(rows: list[GoalFeatureRow]) -> np.ndarray:
    return np.array([r.goals for r in rows], dtype=float)


@dataclass(frozen=True)
class FittedGoalModel:
    name: str
    feature_names: tuple[str, ...]
    predict_fn: Callable[[np.ndarray], np.ndarray]
    nb_alpha: float | None = None


# ---------------------------------------------------------------------------
# Poisson regression - comparison model; expected to underestimate P(0) and
# tail variance given the confirmed overdispersion/zero-inflation.
# ---------------------------------------------------------------------------


def fit_poisson_regression(train_rows: list[GoalFeatureRow], feature_names: tuple[str, ...]) -> FittedGoalModel:
    import statsmodels.api as sm
    from sklearn.impute import SimpleImputer

    X = feature_matrix(train_rows, feature_names)
    y = target_vector(train_rows)
    imputer = SimpleImputer(strategy="median").fit(X)
    X_imp = sm.add_constant(imputer.transform(X), has_constant="add")
    model = sm.GLM(y, X_imp, family=sm.families.Poisson()).fit()

    def predict_fn(X_new: np.ndarray) -> np.ndarray:
        X_new_imp = sm.add_constant(imputer.transform(X_new), has_constant="add")
        return np.clip(model.predict(X_new_imp), 1e-6, None)

    return FittedGoalModel(name="poisson_regression", feature_names=feature_names, predict_fn=predict_fn)


# ---------------------------------------------------------------------------
# Negative Binomial regression - the main single-process count model.
# ---------------------------------------------------------------------------


def fit_negative_binomial_regression(train_rows: list[GoalFeatureRow], feature_names: tuple[str, ...]) -> FittedGoalModel:
    import statsmodels.api as sm
    from sklearn.impute import SimpleImputer
    from statsmodels.discrete.discrete_model import NegativeBinomial

    X = feature_matrix(train_rows, feature_names)
    y = target_vector(train_rows)
    imputer = SimpleImputer(strategy="median").fit(X)
    X_imp = sm.add_constant(imputer.transform(X), has_constant="add")
    model = NegativeBinomial(y, X_imp, loglike_method="nb2").fit(disp=False, maxiter=200)
    alpha = float(model.params[-1])

    def predict_fn(X_new: np.ndarray) -> np.ndarray:
        X_new_imp = sm.add_constant(imputer.transform(X_new), has_constant="add")
        return np.clip(model.predict(X_new_imp), 1e-6, None)

    return FittedGoalModel(name="negative_binomial", feature_names=feature_names, predict_fn=predict_fn, nb_alpha=max(alpha, 1e-6))


# ---------------------------------------------------------------------------
# Hurdle model - two independent pieces (Section 7): a classifier for
# P(scores >= 1), and an NB regression fit ONLY on rows where the player
# scored, describing the conditional count given scoring. Combined into a
# HurdleDistribution (goal_distribution.py) at prediction time.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FittedHurdleModel:
    feature_names: tuple[str, ...]
    predict_p_score: Callable[[np.ndarray], np.ndarray]
    predict_mu_scored: Callable[[np.ndarray], np.ndarray]
    alpha_scored: float


def fit_hurdle_model(train_rows: list[GoalFeatureRow], feature_names: tuple[str, ...]) -> FittedHurdleModel:
    import statsmodels.api as sm
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from statsmodels.discrete.discrete_model import NegativeBinomial

    X = feature_matrix(train_rows, feature_names)
    y = target_vector(train_rows)
    scored_mask = y >= 1

    # Part 1: P(scores >= 1) - a plain regularised logistic classifier,
    # same imputer+scaler pipeline convention as disposal_models.fit_ridge.
    imputer1 = SimpleImputer(strategy="median").fit(X)
    scaler1 = StandardScaler().fit(imputer1.transform(X))
    clf = LogisticRegression(C=1.0, max_iter=1000, random_state=RANDOM_STATE)
    clf.fit(scaler1.transform(imputer1.transform(X)), scored_mask.astype(float))
    positive_class_idx = list(clf.classes_).index(1.0)

    def predict_p_score(X_new: np.ndarray) -> np.ndarray:
        return clf.predict_proba(scaler1.transform(imputer1.transform(X_new)))[:, positive_class_idx]

    # Part 2: conditional count given scoring - NB regression fit only on
    # the scored subset. This is a pragmatic (not exact zero-truncated-MLE)
    # fit: fitting NB directly on y>=1 data slightly overstates the
    # underlying rate vs a true truncated-likelihood fit, but keeps the
    # implementation simple/robust and still produces a valid, correctly
    # zero-truncated distribution once renormalised in HurdleDistribution -
    # see goal_analysis.compare_hurdle_vs_nb for the real-data validation
    # of whether this approximation calibrates well in practice.
    X_scored = X[scored_mask]
    y_scored = y[scored_mask]
    imputer2 = SimpleImputer(strategy="median").fit(X_scored)
    X_scored_imp = sm.add_constant(imputer2.transform(X_scored), has_constant="add")
    count_model = NegativeBinomial(y_scored, X_scored_imp, loglike_method="nb2").fit(disp=False, maxiter=200)
    alpha_scored = max(float(count_model.params[-1]), 1e-6)

    def predict_mu_scored(X_new: np.ndarray) -> np.ndarray:
        X_new_imp = sm.add_constant(imputer2.transform(X_new), has_constant="add")
        return np.clip(count_model.predict(X_new_imp), 1e-6, None)

    return FittedHurdleModel(
        feature_names=feature_names, predict_p_score=predict_p_score, predict_mu_scored=predict_mu_scored, alpha_scored=alpha_scored
    )


# ---------------------------------------------------------------------------
# Gradient boosting regression (XGBoost / LightGBM) - flexible non-linear
# point predictor; wrapped into an NB distribution via residual-fit alpha
# (see disposal_models.fit_residual_nb_alpha), same as disposals.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoostingGoalConfig:
    library: str
    max_depth: int = 3
    learning_rate: float = 0.05
    n_estimators: int = 200
    min_child_weight: float = 5.0
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_alpha: float = 0.0
    reg_lambda: float = 1.0
    random_state: int = RANDOM_STATE


def _build_boosting_regressor(config: BoostingGoalConfig):
    if config.library == "xgboost":
        import xgboost as xgb

        return xgb.XGBRegressor(
            max_depth=config.max_depth,
            learning_rate=config.learning_rate,
            n_estimators=config.n_estimators,
            min_child_weight=config.min_child_weight,
            subsample=config.subsample,
            colsample_bytree=config.colsample_bytree,
            reg_alpha=config.reg_alpha,
            reg_lambda=config.reg_lambda,
            random_state=config.random_state,
            objective="count:poisson",  # goals are a natural count target - unlike disposals' squared-error default
            n_jobs=1,
        )
    if config.library == "lightgbm":
        import lightgbm as lgb

        return lgb.LGBMRegressor(
            max_depth=config.max_depth,
            learning_rate=config.learning_rate,
            n_estimators=config.n_estimators,
            min_child_samples=max(1, int(config.min_child_weight)),
            subsample=config.subsample,
            subsample_freq=1,
            colsample_bytree=config.colsample_bytree,
            reg_alpha=config.reg_alpha,
            reg_lambda=config.reg_lambda,
            random_state=config.random_state,
            objective="poisson",
            verbose=-1,
            n_jobs=1,
        )
    raise ValueError(f"unknown boosting library {config.library!r}")


def fit_boosting_regression(
    train_rows: list[GoalFeatureRow], feature_names: tuple[str, ...], config: BoostingGoalConfig
) -> FittedGoalModel:
    X = feature_matrix(train_rows, feature_names)
    y = target_vector(train_rows)
    model = _build_boosting_regressor(config)
    model.fit(X, y)

    def predict_fn(X_new: np.ndarray) -> np.ndarray:
        return np.clip(model.predict(X_new), 1e-6, None)

    return FittedGoalModel(name=f"gbm_{config.library}", feature_names=feature_names, predict_fn=predict_fn)


__all__ = [
    "FittedGoalModel",
    "FittedHurdleModel",
    "BoostingGoalConfig",
    "feature_matrix",
    "target_vector",
    "fit_poisson_regression",
    "fit_negative_binomial_regression",
    "fit_hurdle_model",
    "fit_boosting_regression",
    "fit_residual_nb_alpha",
]
