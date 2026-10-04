"""Participation features: V1's pregame features plus a few that describe how
often a player has been taking the floor, all read with the same as-of rule
(a box score counts only if final and tipped off at least 4 hours before
the row's cutoff).

Computed over the player's box-score LISTINGS (did-not-play rows included):
- play_rate5:   share of his last 5 listings in which he played
- min0_mean5/10: mean minutes over his last 5/10 listings, DNP counted as 0
- rot_rate10:   share of his last 10 listings with >= ROTATION_MINUTES
- listings_known: how many listings are known (capped at 82)
- team_games_unlisted: games his (candidate) team played, per the schedule,
  since his latest listing - an inactive / injured / departed streak (capped at 20)
- no_history:   1 if he has no earlier PLAYED game in the window (V1-ineligible)

No did-not-play REASON is used anywhere - only whether he played.
"""

import numpy as np
import pandas as pd

from app.nba.minutes.features import ALL_FEATURES, _asof, _final_competitive_games, _team_games_between

ROTATION_MINUTES = 10.0
CAMEO_MINUTES = 5.0

EXTRA_FEATURES = ["play_rate5", "min0_mean5", "min0_mean10", "rot_rate10", "listings_known", "team_games_unlisted", "no_history"]
PARTICIPATION_FEATURES = ALL_FEATURES + EXTRA_FEATURES


def listing_history(logs: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    final = _final_competitive_games(games)[["game_id", "scheduled_start"]]
    rows = logs.merge(final, on="game_id", how="inner").sort_values(["player_id", "scheduled_start", "game_id"]).reset_index(drop=True)
    rows["played"] = (~rows["did_not_play"]).astype(float)
    # Minutes with DNP as 0; NaN when he played but the source recorded no minutes.
    rows["m0"] = np.where(rows["did_not_play"], 0.0, rows["minutes"])
    rows["rot"] = np.where(np.isnan(rows["m0"]), np.nan, (rows["m0"] >= ROTATION_MINUTES).astype(float))
    g = rows.groupby("player_id", sort=False)
    rows["play_rate5"] = g["played"].transform(lambda s: s.rolling(5, min_periods=1).mean())
    rows["min0_mean5"] = g["m0"].transform(lambda s: s.rolling(5, min_periods=1).mean())
    rows["min0_mean10"] = g["m0"].transform(lambda s: s.rolling(10, min_periods=1).mean())
    rows["rot_rate10"] = g["rot"].transform(lambda s: s.rolling(10, min_periods=1).mean())
    rows["listings_known"] = g.cumcount() + 1.0
    return rows


def add_participation_features(base: pd.DataFrame, games: pd.DataFrame, logs: pd.DataFrame) -> pd.DataFrame:
    """`base` is build_features() output (it carries each row's t_cut)."""
    out = base.copy()
    hist = listing_history(logs, games)
    cols = ["play_rate5", "min0_mean5", "min0_mean10", "rot_rate10", "listings_known"]
    h = _asof(out, hist, "player_id", cols)
    for c in cols:
        out[c] = h[c].to_numpy()
    out["listings_known"] = out["listings_known"].fillna(0.0).clip(upper=82.0)
    since = out[["team_id", "scheduled_start"]].assign(history_last_start=h["_hist_start"].to_numpy())
    out["team_games_unlisted"] = np.minimum(_team_games_between(games, since), 20.0)
    out["no_history"] = (~out["eligible"].astype(bool)).astype(float)
    return out
