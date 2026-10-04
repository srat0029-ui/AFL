"""Chronological evaluation of participation / rotation classifiers.

Exactly V1's protocol: season walk-forward, fold S fitted on seasons < S,
hyperparameters chosen by an inner split (train < S-1, validate S-1) on log
loss; selection folds 2020-21..2023-24 decide, test folds 2024-25 and
2025-26 only report. Every model and baseline is scored on the same rows.
"""

import time

import numpy as np
import pandas as pd

from app.nba.minutes.evaluation import FIRST_SEASON, SELECTION_FOLDS, TEST_FOLDS
from app.nba.rotation.features import PARTICIPATION_FEATURES
from app.nba.rotation.metrics import confusion_at, prob_metrics, reliability
from app.nba.rotation.models import BASELINES, CLASSIFIERS, fit_predict_proba


def tune_clf(name: str, rows: pd.DataFrame, train_seasons, val_season: int, features: list[str], target: str) -> tuple[dict, list[dict]]:
    cand = CLASSIFIERS[name]
    if len(cand.grid) == 1:
        return cand.grid[0], []
    train = rows[rows["season_start_year"].isin(train_seasons)]
    val = rows[rows["season_start_year"] == val_season]
    scores = []
    for params in cand.grid:
        _, p = fit_predict_proba(cand, params, train, val, features, target)
        scores.append({"params": params, "val_log_loss": prob_metrics(p, val[target])["log_loss"]})
    best = min(scores, key=lambda s: s["val_log_loss"])
    return best["params"], scores


def walk_forward_clf(data: pd.DataFrame, target: str, names: list[str], features: list[str] = PARTICIPATION_FEATURES, folds=SELECTION_FOLDS + TEST_FOLDS, log=print):
    rows = data[data[target].notna()]
    preds, meta = [], {}
    for season in folds:
        train = rows[rows["season_start_year"] < season]
        test = rows[rows["season_start_year"] == season].copy()
        meta[season] = {"n_train": len(train), "n_test": len(test), "models": {}}
        for b, fn in BASELINES.items():
            test[b] = fn(train, test, target)
        for name in names:
            t0 = time.time()
            params, scores = tune_clf(name, rows, list(range(FIRST_SEASON, season - 1)), season - 1, features, target)
            _, test[name] = fit_predict_proba(CLASSIFIERS[name], params, train, test, features, target)
            meta[season]["models"][name] = {"params": params, "tuning": scores}
            log(f"  {target} fold {season} {name}: {time.time() - t0:.0f}s {params}")
        preds.append(test)
    return pd.concat(preds), meta


def summarize_clf(preds: pd.DataFrame, cols: list[str], target: str, seasons) -> dict:
    sub = preds[preds["season_start_year"].isin(seasons)]
    return {c: prob_metrics(sub[c], sub[target]) for c in cols}


def calibration_report(preds: pd.DataFrame, col: str, target: str, seasons) -> dict:
    sub = preds[preds["season_start_year"].isin(seasons)]
    return {"reliability": reliability(sub[col], sub[target]), "confusion": confusion_at(sub[col], sub[target])}


def segment_clf(preds: pd.DataFrame, cols: list[str], target: str, seasons) -> dict:
    sub = preds[preds["season_start_year"].isin(seasons)].copy()
    segs = {
        "history": np.where(sub["no_history"] == 1.0, "no_history", "has_history"),
        "last_listing": np.select([sub["dnp_last_listed"].isna(), sub["dnp_last_listed"] == 1.0], ["none", "dnp"], default="played"),
        "team_change": np.where(sub["traded"] == 1.0, "first_game_new_team", np.where(sub["games_with_team"] < 10, "new_team_<10", "settled")),
        "absence": np.where(sub["team_games_missed"] >= 5, "missed_5+", np.where(sub["team_games_missed"] >= 1, "missed_1-4", "none")),
        "season_type": sub["season_type"].to_numpy(),
        "listing_recency": pd.cut(sub["team_games_unlisted"], [-1, 0, 2, 9, 99], labels=["listed_last_team_game", "unlisted_1-2", "unlisted_3-9", "unlisted_10+"]).astype(str),
    }
    out = {}
    for seg, labels in segs.items():
        sub["_seg"] = labels
        out[seg] = {str(k): {c: prob_metrics(g[c], g[target]) for c in cols} for k, g in sub.groupby("_seg") if len(g) >= 50 and 0 < g[target].mean() < 1}
    return out
