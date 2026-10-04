"""Chronological evaluation of baselines and model candidates.

No random splits anywhere. Two schemes, both by season:

- Walk-forward: for each evaluation season S, fit on seasons < S only and
  predict S. Hyperparameters for fold S are chosen by an INNER chronological
  split - train on seasons < S-1, validate on S-1 - so the choice for S never
  sees S or anything after it.
- Season holdout: fit on 2018-19..2023-24 (tuned on 2023-24 with training
  through 2022-23), then predict 2024-25 and 2025-26 with that one frozen
  model - the second season tests a model that is a year stale.

Pre-registered protocol (decided before looking at any test season):
- Selection folds: 2020-21..2023-24. Model V1 is chosen on their pooled MAE
  (ties within 0.02 min go to the simpler model).
- Test folds: 2024-25 and 2025-26. Reported for every model, used for
  nothing else.
- Every model and baseline is scored on the same rows: played games with
  recorded minutes, for players eligible under rule E1.
"""

import time

import numpy as np
import pandas as pd

from app.nba.minutes.features import ALL_FEATURES
from app.nba.minutes.metrics import breakdown, point_metrics
from app.nba.minutes.models import CANDIDATES, baseline_predictions, fit_predict

FIRST_SEASON = 2018
SELECTION_FOLDS = (2020, 2021, 2022, 2023)
TEST_FOLDS = (2024, 2025)


def model_rows(data: pd.DataFrame) -> pd.DataFrame:
    return data[data["played"] & data["eligible"]]


def tune(candidate_name: str, rows: pd.DataFrame, train_seasons, val_season: int, features: list[str]) -> tuple[dict, list[dict]]:
    candidate = CANDIDATES[candidate_name]
    if len(candidate.grid) == 1:
        return candidate.grid[0], []
    train = rows[rows["season_start_year"].isin(train_seasons)]
    val = rows[rows["season_start_year"] == val_season]
    scores = []
    for params in candidate.grid:
        _, pred = fit_predict(candidate, params, train, val, features)
        scores.append({"params": params, "val_mae": float(np.abs(pred - val["minutes"].to_numpy()).mean())})
    best = min(scores, key=lambda s: s["val_mae"])
    return best["params"], scores


def walk_forward(data: pd.DataFrame, candidate_names: list[str], features: list[str] = ALL_FEATURES, folds=SELECTION_FOLDS + TEST_FOLDS, log=print) -> tuple[pd.DataFrame, dict]:
    rows = model_rows(data)
    eligible = data[data["eligible"]]
    preds = []
    fold_meta: dict = {}
    for season in folds:
        # Predict every eligible listing (DNP rows too, for the unconditional
        # comparison); the primary metrics use played rows only.
        test = eligible[eligible["season_start_year"] == season].copy()
        train = rows[rows["season_start_year"] < season]
        out = baseline_predictions(test)
        fold_meta[season] = {"train_seasons": sorted(train["season_start_year"].unique().tolist()), "n_train": len(train), "n_test": len(test), "models": {}}
        for name in candidate_names:
            t0 = time.time()
            inner_train = list(range(FIRST_SEASON, season - 1))
            params, scores = tune(name, rows, inner_train, season - 1, features)
            _, pred = fit_predict(CANDIDATES[name], params, train, test, features)
            out[name] = pred
            fold_meta[season]["models"][name] = {"params": params, "tuning": scores, "seconds": round(time.time() - t0, 1)}
            log(f"  fold {season} {name}: {time.time() - t0:.0f}s params={params}")
        preds.append(pd.concat([test, out], axis=1))
    return pd.concat(preds), fold_meta


def season_holdout(data: pd.DataFrame, candidate_names: list[str], features: list[str] = ALL_FEATURES, train_through: int = 2023, test_seasons=TEST_FOLDS, log=print):
    rows = model_rows(data)
    train = rows[rows["season_start_year"] <= train_through]
    test = rows[rows["season_start_year"].isin(test_seasons)].copy()  # played rows only
    out = baseline_predictions(test)
    meta: dict = {"train_seasons": sorted(train["season_start_year"].unique().tolist()), "test_seasons": list(test_seasons), "models": {}}
    for name in candidate_names:
        params, scores = tune(name, rows, list(range(FIRST_SEASON, train_through)), train_through, features)
        _, pred = fit_predict(CANDIDATES[name], params, train, test, features)
        out[name] = pred
        meta["models"][name] = {"params": params, "tuning": scores}
        log(f"  holdout {name}: params={params}")
    return pd.concat([test, out], axis=1), meta


# --- segments (all defined from PRE-GAME information) ------------------------


def add_segments(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    f["seg_role"] = np.where(f["started_last"] == 1.0, "starter_last_game", "bench_last_game")
    f["seg_minutes_band"] = pd.cut(f["min_mean10"], [-0.1, 15, 25, 32, 60], labels=["<15", "15-25", "25-32", "32+"])
    f["seg_stability"] = pd.cut(f["min_std10"].fillna(99), [-0.1, 3.5, 6.5, 1000], labels=["stable(sd<=3.5)", "medium", "volatile(sd>6.5 or <3 games)"])
    f["seg_season_type"] = f["season_type"]
    changed = (f["games_with_team"] < 10) & (f["career_games"] > f["games_with_team"])
    f["seg_team_change"] = np.where(f["traded"] == 1.0, "first_game_new_team", np.where(changed, "new_team_<10_games", "settled"))
    f["seg_absence"] = np.where(f["team_games_missed"] >= 5, "returning_5+_team_games_missed", np.where(f["team_games_missed"] >= 1, "missed_1-4", "no_absence"))
    f["seg_early_season"] = np.where(f["season_games"] < 5, "first_5_games_of_season", "later")
    f["seg_back_to_back"] = np.where(f["back_to_back"] == 1.0, "back_to_back", "rested")
    return f


SEGMENTS = ["seg_role", "seg_minutes_band", "seg_stability", "seg_season_type", "seg_team_change", "seg_absence", "seg_early_season", "seg_back_to_back"]


def summarize(preds: pd.DataFrame, model_cols: list[str], seasons, target: str = "minutes") -> dict:
    sub = preds[preds["season_start_year"].isin(seasons)]
    sub = sub[sub["played"]] if target == "minutes" else sub[sub[target].notna()]
    return {m: point_metrics(sub[m], sub[target]) for m in model_cols}


def segment_report(preds: pd.DataFrame, model_cols: list[str], seasons) -> dict:
    sub = preds[preds["season_start_year"].isin(seasons) & preds["played"]]
    sub = add_segments(sub)
    return {seg: {m: breakdown(sub, m, "minutes", seg) for m in model_cols} for seg in SEGMENTS}
