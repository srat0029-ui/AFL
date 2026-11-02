"""Minutes V2 teammate-absence features for game Y.

Computed per roster candidate from his roster teammates in the same
team-game, given each teammate's probability of dressing q_j:

- dressed_others       sum q_j
- absent_new_min       sum (1 - q_j) * EWMA_j  over teammates listed in the team's last game
- absent_new_n         sum (1 - q_j)           over the same
- absent_new_starters  sum (1 - q_j)           over the same who are regular starters
- absent_recent_min    sum (1 - q_j) * EWMA_j  over teammates last listed within the team's last 5 games
- returning_min        sum q_j * EWMA_j        over teammates who missed the team's last game
- ewm_rank_dressed     1 + sum q_j over teammates with more EWMA minutes than him

These are expectations over who dresses. With q_j in {0, 1} (the box-score
listing - the oracle used in step 1) they are exactly the oracle features.
Every sum excludes the row's own player. Inputs other than q are V1's as-of
history features (min_ewm, start_rate5, team_games_unlisted).
"""

import numpy as np
import pandas as pd

REGULAR_STARTER_RATE = 0.6  # start_rate5 at or above this: a regular starter
RECENT_TEAM_GAMES = 4  # "recent": last listed within the team's last 5 games

TEAMMATE_FEATURES = [
    "dressed_others",
    "absent_new_min",
    "absent_new_n",
    "absent_new_starters",
    "absent_recent_min",
    "returning_min",
    "ewm_rank_dressed",
]


def expected_teammate_features(frame: pd.DataFrame, q) -> pd.DataFrame:
    """TEAMMATE_FEATURES for every row of a roster-universe frame (one row per
    candidate per team-game), given q = each row's probability of dressing."""
    q = np.clip(np.asarray(q, dtype=float), 0.0, 1.0)
    if np.isnan(q).any():
        raise ValueError("q must be complete")
    key = [frame["game_id"].to_numpy(), frame["team_id"].to_numpy()]
    ewm = frame["min_ewm"].fillna(0.0).to_numpy(dtype=float)
    unlisted = frame["team_games_unlisted"].fillna(20.0).to_numpy(dtype=float)
    starter = frame["start_rate5"].fillna(0.0).to_numpy(dtype=float) >= REGULAR_STARTER_RATE
    new = unlisted == 0
    recent = unlisted <= RECENT_TEAM_GAMES
    absent = 1.0 - q

    def others(values) -> np.ndarray:
        s = pd.Series(np.asarray(values, dtype=float), index=frame.index)
        return (s.groupby(key).transform("sum") - s).to_numpy()

    out = pd.DataFrame(index=frame.index)
    out["dressed_others"] = others(q)
    out["absent_new_min"] = others(absent * ewm * new)
    out["absent_new_n"] = others(absent * new)
    out["absent_new_starters"] = others(absent * (new & starter))
    out["absent_recent_min"] = others(absent * ewm * recent)
    out["returning_min"] = others(q * ewm * ~new)
    rank = np.ones(len(frame))
    pos = pd.Series(np.arange(len(frame)), index=frame.index)
    for _, idx in pos.groupby(key):
        i = idx.to_numpy()
        greater = ewm[i][None, :] > ewm[i][:, None]  # [row, teammate]: teammate has more EWMA minutes
        rank[i] = 1.0 + (greater * q[i][None, :]).sum(axis=1)
    out["ewm_rank_dressed"] = rank
    return out


def oracle_teammate_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Step 1's oracle: who actually dressed (the box-score listing)."""
    return expected_teammate_features(frame, frame["listed"].astype(bool).astype(float))
