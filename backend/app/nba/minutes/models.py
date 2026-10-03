"""Baselines and model candidates for expected minutes.

Baselines need no fitting - each is a single history feature, defined for
every eligible row, so every model and baseline is scored on the SAME rows.

Every candidate is a scikit-learn-compatible estimator whose preprocessing
(imputation, scaling) lives inside the estimator/pipeline, so `fit` on the
training rows is the only place any parameter - including an imputation
median or a scaling mean - is learned. Nothing is fitted on evaluation rows.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

# --- baselines -------------------------------------------------------------


def _season_to_date(frame: pd.DataFrame) -> pd.Series:
    # First game of a season has no season-to-date average: fall back to the
    # last-10 average so the baseline covers the same rows as the others.
    return frame["season_mean"].fillna(frame["min_mean10"])


BASELINES = {
    "last_game": lambda f: f["min_last1"],
    "last_3_mean": lambda f: f["min_mean3"],
    "last_5_mean": lambda f: f["min_mean5"],
    "last_10_mean": lambda f: f["min_mean10"],
    "season_to_date_mean": _season_to_date,
    "ewma_halflife_4": lambda f: f["min_ewm"],
}


def baseline_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({name: fn(frame).astype(float) for name, fn in BASELINES.items()}, index=frame.index)
    if out.isna().any().any():
        raise ValueError("a baseline is undefined for an eligible row - eligibility and baselines are out of step")
    return out


# --- model candidates --------------------------------------------------------


class DropEmptyColumns(RegressorMixin, BaseEstimator):
    """Wraps an estimator and drops feature columns that have NO observed
    value in the training data (e.g. previous-season minutes when training
    on the first modelled season only). Which columns to drop is decided in
    `fit` from training rows alone and then applied unchanged at predict."""

    def __init__(self, estimator):
        self.estimator = estimator

    def fit(self, X, y):
        X = np.asarray(X, dtype=float)
        self.kept_ = ~np.isnan(X).all(axis=0)
        self.estimator.fit(X[:, self.kept_], y)
        return self

    def predict(self, X):
        return self.estimator.predict(np.asarray(X, dtype=float)[:, self.kept_])


@dataclass(frozen=True)
class Candidate:
    name: str
    description: str
    grid: list[dict] = field(default_factory=lambda: [{}])

    def build(self, params: dict):
        raise NotImplementedError


class RidgeCandidate(Candidate):
    def build(self, params: dict) -> Pipeline:
        return make_pipeline(
            SimpleImputer(strategy="median", add_indicator=True),
            StandardScaler(),
            Ridge(alpha=params.get("alpha", 10.0)),
        )


class HgbCandidate(Candidate):
    def build(self, params: dict) -> HistGradientBoostingRegressor:
        return HistGradientBoostingRegressor(
            loss=params.get("loss", "squared_error"),
            learning_rate=params.get("learning_rate", 0.05),
            max_iter=params.get("max_iter", 400),
            max_leaf_nodes=params.get("max_leaf_nodes", 31),
            min_samples_leaf=params.get("min_samples_leaf", 50),
            l2_regularization=params.get("l2_regularization", 1.0),
            early_stopping=False,  # no internal random validation split: fully deterministic
            random_state=0,
        )


class LgbmCandidate(Candidate):
    def build(self, params: dict):
        from lightgbm import LGBMRegressor  # already a project dependency

        return LGBMRegressor(
            n_estimators=params.get("n_estimators", 600),
            learning_rate=params.get("learning_rate", 0.03),
            num_leaves=params.get("num_leaves", 31),
            min_child_samples=params.get("min_child_samples", 50),
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            random_state=0,
            n_jobs=4,
            deterministic=True,
            force_row_wise=True,
            verbose=-1,
        )


class RandomForestCandidate(Candidate):
    def build(self, params: dict) -> Pipeline:
        return make_pipeline(
            SimpleImputer(strategy="median", add_indicator=True),
            RandomForestRegressor(
                n_estimators=params.get("n_estimators", 200),
                min_samples_leaf=params.get("min_samples_leaf", 25),
                max_features=params.get("max_features", 0.33),
                n_jobs=-1,
                random_state=0,
            ),
        )


CANDIDATES: dict[str, Candidate] = {
    "ridge": RidgeCandidate("ridge", "Ridge regression, median imputation + missing indicators + standard scaling", grid=[{"alpha": a} for a in (1.0, 10.0, 100.0)]),
    "hgb": HgbCandidate(
        "hgb",
        "HistGradientBoostingRegressor, squared error (a mean estimator)",
        grid=[{"max_leaf_nodes": leaves, "max_iter": it, "learning_rate": 0.05} for leaves in (15, 31) for it in (200, 500)],
    ),
    "hgb_l1": HgbCandidate(
        "hgb_l1",
        "HistGradientBoostingRegressor, absolute error (a MEDIAN estimator - reference only)",
        grid=[{"loss": "absolute_error", "max_leaf_nodes": 31, "max_iter": it, "learning_rate": 0.05} for it in (200, 500)],
    ),
    "lgbm": LgbmCandidate(
        "lgbm",
        "LightGBM, squared error (existing dependency)",
        grid=[{"num_leaves": leaves, "n_estimators": n} for leaves in (15, 31) for n in (300, 800)],
    ),
    "random_forest": RandomForestCandidate("random_forest", "Random forest, 200 trees, min leaf 25, max features 0.33 (fixed, not tuned)"),
}


def fit_predict(candidate: Candidate, params: dict, train: pd.DataFrame, test: pd.DataFrame, features: list[str], target: str = "minutes"):
    model = DropEmptyColumns(candidate.build(params))
    model.fit(train[features].to_numpy(dtype=float), train[target].to_numpy(dtype=float))
    pred = model.predict(test[features].to_numpy(dtype=float))
    return model, np.clip(pred, 0.0, 60.0)
