"""Candidate point-prediction models for disposals — Section 7 of the
disposal-prediction stage brief. Every model here predicts a single number
(expected disposals); turning that into a full distribution/threshold
probabilities is disposal_distribution.py's job, kept deliberately separate
so a model can be swapped without touching distribution logic.

Same feature_matrix convention as app/modelling/logistic.py/boosting.py:
missing feature values become NaN, not a guessed default - each model
handles NaN according to its own requirements (GBM natively; Ridge/GLMs via
median imputation, fit on the TRAINING split only and reused for
prediction, never re-fit on eval data - see fit_* functions).
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from app.player_modelling.disposal_features import DisposalFeatureRow

RANDOM_STATE = 42


def feature_matrix(rows: list[DisposalFeatureRow], feature_names: tuple[str, ...]) -> np.ndarray:
    return np.array(
        [[row.features.get(name) if row.features.get(name) is not None else np.nan for name in feature_names] for row in rows],
        dtype=float,
    )


def target_vector(rows: list[DisposalFeatureRow]) -> np.ndarray:
    return np.array([r.disposals for r in rows], dtype=float)


@dataclass(frozen=True)
class FittedDisposalModel:
    """What every candidate model produces: a name, a plain
    ndarray-in/ndarray-out predict function (so disposal_backtest.py can
    treat all of them uniformly), and — only for the count-regression
    models — a fitted NB dispersion (alpha) usable directly for
    disposal_distribution.NegativeBinomialDistribution. Ridge/GBM leave
    nb_alpha None; disposal_backtest.py falls back to a dispersion fit on
    that model's own residuals instead (see fit_residual_nb_alpha)."""

    name: str
    feature_names: tuple[str, ...]
    predict_fn: Callable[[np.ndarray], np.ndarray]
    nb_alpha: float | None = None


# ---------------------------------------------------------------------------
# Baseline-comparable but fitted: Ridge (regularised linear) regression
# ---------------------------------------------------------------------------


def fit_ridge(train_rows: list[DisposalFeatureRow], feature_names: tuple[str, ...], alpha: float = 5.0) -> FittedDisposalModel:
    X = feature_matrix(train_rows, feature_names)
    y = target_vector(train_rows)
    imputer = SimpleImputer(strategy="median").fit(X)
    scaler = StandardScaler().fit(imputer.transform(X))
    model = Ridge(alpha=alpha, random_state=RANDOM_STATE)
    model.fit(scaler.transform(imputer.transform(X)), y)

    def predict_fn(X_new: np.ndarray) -> np.ndarray:
        return np.clip(model.predict(scaler.transform(imputer.transform(X_new))), 0, None)

    return FittedDisposalModel(name="ridge", feature_names=feature_names, predict_fn=predict_fn)


# ---------------------------------------------------------------------------
# Poisson regression (statsmodels GLM) — comparison baseline for the count
# models; disposals are overdispersed (see disposal audit / NB section
# below), so Poisson is expected to underestimate spread even if its mean
# predictions are competitive.
# ---------------------------------------------------------------------------


def fit_poisson_regression(train_rows: list[DisposalFeatureRow], feature_names: tuple[str, ...]) -> FittedDisposalModel:
    import statsmodels.api as sm

    X = feature_matrix(train_rows, feature_names)
    y = target_vector(train_rows)
    imputer = SimpleImputer(strategy="median").fit(X)
    X_imp = sm.add_constant(imputer.transform(X), has_constant="add")
    model = sm.GLM(y, X_imp, family=sm.families.Poisson()).fit()

    def predict_fn(X_new: np.ndarray) -> np.ndarray:
        X_new_imp = sm.add_constant(imputer.transform(X_new), has_constant="add")
        return np.clip(model.predict(X_new_imp), 0, None)

    return FittedDisposalModel(name="poisson_regression", feature_names=feature_names, predict_fn=predict_fn)


# ---------------------------------------------------------------------------
# Negative Binomial regression (statsmodels GLM, NB2) — fits its own
# dispersion (alpha) directly from training data via statsmodels'
# NegativeBinomial family, rather than estimating it separately from
# residuals afterward (see disposal_distribution.py's module docstring for
# why overdispersion is expected).
# ---------------------------------------------------------------------------


def fit_negative_binomial_regression(
    train_rows: list[DisposalFeatureRow], feature_names: tuple[str, ...]
) -> FittedDisposalModel:
    import statsmodels.api as sm
    from statsmodels.discrete.discrete_model import NegativeBinomial

    X = feature_matrix(train_rows, feature_names)
    y = target_vector(train_rows)
    imputer = SimpleImputer(strategy="median").fit(X)
    X_imp = sm.add_constant(imputer.transform(X), has_constant="add")
    model = NegativeBinomial(y, X_imp, loglike_method="nb2").fit(disp=False, maxiter=200)
    alpha = float(model.params[-1])  # statsmodels appends the NB dispersion as the last fitted parameter

    def predict_fn(X_new: np.ndarray) -> np.ndarray:
        X_new_imp = sm.add_constant(imputer.transform(X_new), has_constant="add")
        return np.clip(model.predict(X_new_imp), 0, None)

    return FittedDisposalModel(name="negative_binomial", feature_names=feature_names, predict_fn=predict_fn, nb_alpha=max(alpha, 1e-6))


# ---------------------------------------------------------------------------
# Gradient boosting regression (XGBoost / LightGBM) — the non-linear
# candidate; can learn interactions a linear/GLM model structurally can't.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoostingRegressionConfig:
    library: str  # "xgboost" | "lightgbm"
    max_depth: int = 3
    learning_rate: float = 0.05
    n_estimators: int = 200
    min_child_weight: float = 5.0
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_alpha: float = 0.0
    reg_lambda: float = 1.0
    random_state: int = RANDOM_STATE


def _build_boosting_regressor(config: BoostingRegressionConfig):
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
            objective="reg:squarederror",
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
            verbose=-1,
            n_jobs=1,
        )
    raise ValueError(f"unknown boosting library {config.library!r}")


def fit_boosting_regression(
    train_rows: list[DisposalFeatureRow], feature_names: tuple[str, ...], config: BoostingRegressionConfig
) -> FittedDisposalModel:
    X = feature_matrix(train_rows, feature_names)
    y = target_vector(train_rows)
    model = _build_boosting_regressor(config)
    model.fit(X, y)

    def predict_fn(X_new: np.ndarray) -> np.ndarray:
        return np.clip(model.predict(X_new), 0, None)

    return FittedDisposalModel(name=f"gbm_{config.library}", feature_names=feature_names, predict_fn=predict_fn)


# ---------------------------------------------------------------------------
# Shared: fit an NB dispersion from a model's own residuals, for models that
# don't produce one directly (Ridge, GBM) — a method-of-moments estimate
# (Var = mu + alpha*mu^2 => alpha = (Var - mean(mu)) / mean(mu^2), floored
# at a small positive value so a model with negative apparent overdispersion
# degrades gracefully toward Poisson rather than producing an invalid PMF).
# ---------------------------------------------------------------------------


def fit_residual_nb_alpha(predicted: np.ndarray, actual: np.ndarray) -> float:
    residual_var = float(np.var(actual - predicted, ddof=1))
    mean_mu = float(np.mean(predicted))
    mean_mu2 = float(np.mean(predicted**2))
    if mean_mu2 <= 0:
        return 1e-6
    alpha = (residual_var - mean_mu) / mean_mu2
    return max(alpha, 1e-6)
