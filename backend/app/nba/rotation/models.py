"""Participation / rotation classifiers and their baselines.

As in V1, every learned parameter - including imputation medians, scaling,
which all-empty columns to drop, and the baselines' rates - is fitted on
training rows only, inside fit().
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

P_CLIP = 1e-4


class DropEmptyColumnsClassifier(ClassifierMixin, BaseEstimator):
    """Drops feature columns with no observed value in the TRAINING data
    (decided in fit, applied unchanged at predict)."""

    def __init__(self, estimator):
        self.estimator = estimator

    def fit(self, X, y):
        X = np.asarray(X, dtype=float)
        self.kept_ = ~np.isnan(X).all(axis=0)
        self.estimator.fit(X[:, self.kept_], y)
        self.classes_ = self.estimator.classes_
        return self

    def predict_proba(self, X):
        return self.estimator.predict_proba(np.asarray(X, dtype=float)[:, self.kept_])


@dataclass(frozen=True)
class ClfCandidate:
    name: str
    description: str
    grid: list[dict] = field(default_factory=lambda: [{}])

    def build(self, params: dict):
        raise NotImplementedError


class LogisticCandidate(ClfCandidate):
    def build(self, params: dict):
        return make_pipeline(
            SimpleImputer(strategy="median", add_indicator=True),
            StandardScaler(),
            LogisticRegression(C=params.get("C", 1.0), max_iter=3000),
        )


class HgbClfCandidate(ClfCandidate):
    def build(self, params: dict):
        return HistGradientBoostingClassifier(
            learning_rate=params.get("learning_rate", 0.05),
            max_iter=params.get("max_iter", 300),
            max_leaf_nodes=params.get("max_leaf_nodes", 31),
            min_samples_leaf=params.get("min_samples_leaf", 50),
            l2_regularization=params.get("l2_regularization", 1.0),
            early_stopping=False,
            random_state=0,
        )


class LgbmClfCandidate(ClfCandidate):
    def build(self, params: dict):
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=params.get("n_estimators", 400),
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


CLASSIFIERS: dict[str, ClfCandidate] = {
    "logistic": LogisticCandidate("logistic", "Logistic regression, median imputation + missing indicators + scaling", grid=[{"C": c} for c in (0.1, 1.0)]),
    "hgb": HgbClfCandidate("hgb", "HistGradientBoostingClassifier", grid=[{"max_leaf_nodes": leaves, "max_iter": it} for leaves in (15, 31) for it in (200, 500)]),
    "lgbm": LgbmClfCandidate("lgbm", "LightGBM classifier (existing dependency)", grid=[{"num_leaves": leaves, "n_estimators": n} for leaves in (15, 31) for n in (300, 800)]),
}


def fit_predict_proba(candidate: ClfCandidate, params: dict, train: pd.DataFrame, test: pd.DataFrame, features: list[str], target: str):
    model = DropEmptyColumnsClassifier(candidate.build(params))
    model.fit(train[features].to_numpy(dtype=float), train[target].to_numpy(dtype=int))
    p = model.predict_proba(test[features].to_numpy(dtype=float))[:, 1]
    return model, np.clip(p, P_CLIP, 1 - P_CLIP)


# --- baselines (each fitted on training rows only) -------------------------


def baseline_prevalence(train: pd.DataFrame, test: pd.DataFrame, target: str) -> np.ndarray:
    return np.full(len(test), float(train[target].mean()))


def baseline_last_listing(train: pd.DataFrame, test: pd.DataFrame, target: str) -> np.ndarray:
    """Empirical rate of the target by what happened in the player's previous
    listing: did not play / played under 10 / played 10+ / no previous listing."""

    def state(f: pd.DataFrame) -> pd.Series:
        s = np.select(
            [f["dnp_last_listed"].isna(), f["dnp_last_listed"] == 1.0, f["min_last1"] >= 10.0],
            ["none", "dnp", "rot"],
            default="cameo",
        )
        return pd.Series(s, index=f.index)

    rates = train.groupby(state(train))[target].mean()
    prior = float(train[target].mean())
    return np.clip(state(test).map(rates).fillna(prior).to_numpy(dtype=float), P_CLIP, 1 - P_CLIP)


def baseline_recent_rate(train: pd.DataFrame, test: pd.DataFrame, target: str) -> np.ndarray:
    """The player's own recent rate (played share of last 10 listings, or
    10+-minute share for rotation targets), calibrated by a one-feature
    logistic fit on training rows; players with no listings get the
    training rate for that case."""
    col = "rot_rate10" if target != "y_play" else "play_rate10"

    def x(f: pd.DataFrame) -> np.ndarray:
        rate = (1.0 - f["dnp_rate10"]) if col == "play_rate10" else f["rot_rate10"]
        missing = rate.isna().astype(float)
        return np.column_stack([rate.fillna(0.0).to_numpy(), missing.to_numpy()])

    model = LogisticRegression(max_iter=1000).fit(x(train), train[target].to_numpy(dtype=int))
    return np.clip(model.predict_proba(x(test))[:, 1], P_CLIP, 1 - P_CLIP)


BASELINES = {
    "base_rate": baseline_prevalence,
    "last_listing_state": baseline_last_listing,
    "recent_rate_calibrated": baseline_recent_rate,
}
