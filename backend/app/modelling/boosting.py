"""Gradient boosting for AFL match-winner probability — the non-linear
counterpart to app/modelling/logistic.py's regularised logistic regression.
Same purpose (does the existing point-in-time feature set carry more signal
than Elo alone?), different question: logistic regression can only combine
features linearly; boosting can learn thresholds and interactions
(app/modelling/boosting_interactions.py) that a linear model structurally
can't represent.

Both XGBoost and LightGBM installed cleanly on Windows with no platform
issues, so both are compared rather than picking one on faith — see
app/modelling/boosting_cli.py for the real comparison. This module wraps
whichever library a given BoostingConfig names behind one interface so the
rest of the pipeline (tuning, evaluation, ablation, ensembling) doesn't
care which one produced a given fitted model.

Unlike logistic regression, gradient-boosted trees handle missing values
natively (both libraries learn a split direction for NaN at each node) and
are scale-invariant, so there's no imputer/scaler step here — the raw
(NaN-containing) feature matrix is fed directly to the model. Draws are
still excluded from *fitting* only, for the same reason as logistic.py:
these libraries want binary class labels, not the 0.5 "half-win"
convention used everywhere else in this codebase for scoring.
"""

from dataclasses import dataclass

import numpy as np

from app.modelling.features import MatchFeatureRow
from app.modelling.logistic import feature_matrix

RANDOM_STATE = 42
LIBRARIES = ("xgboost", "lightgbm")


@dataclass(frozen=True)
class BoostingConfig:
    library: str  # "xgboost" | "lightgbm"
    feature_names: tuple[str, ...]
    max_depth: int = 3
    learning_rate: float = 0.05
    n_estimators: int = 100
    min_child_weight: float = 5.0  # xgboost: min_child_weight (float); lightgbm: min_child_samples (int)
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_alpha: float = 0.0
    reg_lambda: float = 1.0
    random_state: int = RANDOM_STATE


@dataclass(frozen=True)
class BoostingPrediction:
    match_id: int
    season_year: int
    home_win_probability: float
    actual_home_outcome: float


def build_model(config: BoostingConfig):
    if config.library == "xgboost":
        import xgboost as xgb

        return xgb.XGBClassifier(
            max_depth=config.max_depth,
            learning_rate=config.learning_rate,
            n_estimators=config.n_estimators,
            min_child_weight=config.min_child_weight,
            subsample=config.subsample,
            colsample_bytree=config.colsample_bytree,
            reg_alpha=config.reg_alpha,
            reg_lambda=config.reg_lambda,
            random_state=config.random_state,
            eval_metric="logloss",
            n_jobs=1,
        )
    if config.library == "lightgbm":
        import lightgbm as lgb

        return lgb.LGBMClassifier(
            max_depth=config.max_depth,
            learning_rate=config.learning_rate,
            n_estimators=config.n_estimators,
            min_child_samples=max(1, int(config.min_child_weight)),
            subsample=config.subsample,
            subsample_freq=1,  # LightGBM ignores `subsample` unless bagging frequency is set
            colsample_bytree=config.colsample_bytree,
            reg_alpha=config.reg_alpha,
            reg_lambda=config.reg_lambda,
            random_state=config.random_state,
            verbose=-1,
            n_jobs=1,
        )
    raise ValueError(f"unknown boosting library {config.library!r} — expected one of {LIBRARIES}")


def fit_boosting_model(train_rows: list[MatchFeatureRow], config: BoostingConfig):
    non_draw = [r for r in train_rows if r.actual_home_outcome != 0.5]
    X = feature_matrix(non_draw, config.feature_names)
    y = np.array([r.actual_home_outcome for r in non_draw])
    model = build_model(config)
    model.fit(X, y)
    return model


def predict(model, rows: list[MatchFeatureRow], feature_names: tuple[str, ...]) -> list[BoostingPrediction]:
    if not rows:
        return []
    X = feature_matrix(rows, feature_names)
    class_index = list(model.classes_).index(1.0)
    probs = model.predict_proba(X)[:, class_index]
    return [
        BoostingPrediction(
            match_id=r.match_id, season_year=r.season_year, home_win_probability=float(p), actual_home_outcome=r.actual_home_outcome
        )
        for r, p in zip(rows, probs)
    ]
