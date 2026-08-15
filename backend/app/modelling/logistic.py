"""Regularised logistic regression for AFL match-winner probability.

A single, interpretable model, deliberately not gradient boosting — this
stage's explicit goal is to see whether a compact, football-defensible
feature set clears Elo's already-strong bar before reaching for more
complexity (see app/modelling/features.py for the feature set itself).

Two variants share this fitting code, differing only in which feature names
are selected: "stats_only" (app.modelling.features.STATS_FEATURE_NAMES) and
"stats_plus_elo" (...STATS_PLUS_ELO_FEATURE_NAMES). Comparing them against
each other and against Elo alone is how this stage answers "do advanced
stats carry information Elo doesn't already have."

Missing feature values (rolling windows below the minimum-history threshold
— see features.py) are imputed with the TRAINING set's median via
sklearn's Pipeline, which by construction never re-fits on data passed to
.predict()/.transform() later — this is what keeps imputation and scaling
leakage-safe without any extra bookkeeping here.

Draws are excluded from *fitting* only: sklearn's LogisticRegression needs
binary class labels, and a draw's 0.5 "half-win" convention (used
everywhere else in this codebase for Brier/log-loss scoring — see
metrics.py) doesn't correspond to a class. A fitted model still produces a
probability for a drawn match when scoring, exactly like Elo and Poisson
already do — this mirrors accuracy()'s existing draw handling, not a new
convention invented for this model.
"""

from dataclasses import dataclass

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.modelling.features import MatchFeatureRow

RANDOM_STATE = 42


@dataclass(frozen=True)
class LogisticConfig:
    feature_names: tuple[str, ...]
    C: float = 1.0  # inverse regularisation strength (sklearn convention: smaller C = stronger regularisation)
    random_state: int = RANDOM_STATE


@dataclass(frozen=True)
class LogisticPrediction:
    match_id: int
    season_year: int
    home_win_probability: float
    actual_home_outcome: float


def feature_matrix(rows: list[MatchFeatureRow], feature_names: tuple[str, ...]) -> np.ndarray:
    return np.array(
        [[row.features.get(name) if row.features.get(name) is not None else np.nan for name in feature_names] for row in rows],
        dtype=float,
    )


def build_pipeline(config: LogisticConfig) -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            (
                "logreg",
                LogisticRegression(
                    C=config.C, solver="lbfgs", max_iter=1000, random_state=config.random_state
                ),
            ),
        ]
    )


def fit_logistic_model(train_rows: list[MatchFeatureRow], config: LogisticConfig) -> Pipeline:
    non_draw = [r for r in train_rows if r.actual_home_outcome != 0.5]
    X = feature_matrix(non_draw, config.feature_names)
    y = np.array([r.actual_home_outcome for r in non_draw])
    pipeline = build_pipeline(config)
    pipeline.fit(X, y)
    return pipeline


def predict(pipeline: Pipeline, rows: list[MatchFeatureRow], feature_names: tuple[str, ...]) -> list[LogisticPrediction]:
    if not rows:
        return []
    X = feature_matrix(rows, feature_names)
    class_index = list(pipeline.classes_).index(1.0)
    probs = pipeline.predict_proba(X)[:, class_index]
    return [
        LogisticPrediction(
            match_id=r.match_id, season_year=r.season_year, home_win_probability=float(p), actual_home_outcome=r.actual_home_outcome
        )
        for r, p in zip(rows, probs)
    ]
