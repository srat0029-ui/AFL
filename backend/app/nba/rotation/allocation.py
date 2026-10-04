"""Team-minutes reconciliation of V1's conditional minutes.

Identity: the minutes actually played by a team sum to 240 plus 25 per
overtime period. Before the game the best statement of that is in
expectation:

    E[team minutes] = sum_i P(play_i) x E(minutes_i | play_i)  ~=  budget

V1 predicts each player independently, so its implied team total can be
off. The reconciliation moves each player's conditional minutes by the
smallest variance-weighted amount that closes a fraction `alpha` of the gap:

    minimise  sum_i (m'_i - m_i)^2 / sigma_i^2
    subject to sum_i p_i m'_i = E + alpha (B - E)
    =>  m'_i = m_i + alpha * lambda * p_i * sigma_i^2,
        lambda = (B - E) / sum_i p_i^2 sigma_i^2

sigma_i is V1's out-of-sample residual SD for the player's predicted-minutes
band (larger for bench and volatile players, about 5 minutes for 34+), so a
confident heavy-minute prediction moves little and the adjustment lands on
the uncertain part of the rotation. alpha = 0 is raw V1; alpha = 1 forces the
expected total onto the budget. Results are clipped to [0, 48] (never below
0, never pushed above 48, and never pulled below a raw value above 48).

The output stays a CONDITIONAL quantity: m'_i is still minutes given he plays.
A proportional variant (m'_i = m_i (B / E)^alpha) is provided for comparison.
"""

import numpy as np
import pandas as pd

from app.nba.minutes.residuals import BAND_LABELS, pred_band

REGULATION_TEAM_MINUTES = 240.0
OVERTIME_TEAM_MINUTES = 25.0


def band_sigma(pred, sigma_by_band: dict) -> np.ndarray:
    bands = pred_band(pred)
    return bands.map({b: sigma_by_band[b] for b in BAND_LABELS}).astype(float).to_numpy()


def reconcile_team(m, p, sigma, budget: float, alpha: float, method: str = "variance") -> np.ndarray:
    m = np.asarray(m, dtype=float)
    p = np.asarray(p, dtype=float)
    expected = float((p * m).sum())
    if alpha == 0.0 or expected <= 0:
        return m.copy()
    if method == "variance":
        w = p * np.asarray(sigma, dtype=float) ** 2
        denom = float((p * w).sum())
        adj = m + alpha * (budget - expected) / denom * w if denom > 0 else m.copy()
    elif method == "proportional":
        adj = m * (budget / expected) ** alpha
    else:
        raise ValueError(f"unknown method {method!r}")
    upper = np.maximum(48.0, m)
    return np.clip(adj, 0.0, upper)


def reconcile_frame(frame: pd.DataFrame, *, m_col: str, p_col: str, sigma_col: str, budget_col: str, alpha: float, method: str = "variance") -> pd.Series:
    """Reconcile every team-game in `frame` (one row per listed player) -
    the vectorised equivalent of reconcile_team per (game_id, team_id)."""
    m = frame[m_col].to_numpy(dtype=float)
    p = frame[p_col].to_numpy(dtype=float)
    keys = [frame["game_id"], frame["team_id"]]
    expected = pd.Series(p * m, index=frame.index).groupby(keys).transform("sum").to_numpy()
    budget = frame[budget_col].to_numpy(dtype=float)
    if alpha == 0.0:
        return pd.Series(m.copy(), index=frame.index)
    if method == "variance":
        w = p * frame[sigma_col].to_numpy(dtype=float) ** 2
        denom = pd.Series(p * w, index=frame.index).groupby(keys).transform("sum").to_numpy()
        adj = np.where((denom > 0) & (expected > 0), m + alpha * (budget - expected) / np.where(denom > 0, denom, 1.0) * w, m)
    elif method == "proportional":
        adj = np.where(expected > 0, m * (budget / np.where(expected > 0, expected, 1.0)) ** alpha, m)
    else:
        raise ValueError(f"unknown method {method!r}")
    return pd.Series(np.clip(adj, 0.0, np.maximum(48.0, m)), index=frame.index)


def team_totals(frame: pd.DataFrame, m_col: str, p_col: str) -> pd.DataFrame:
    """Per team-game: expected total (sum p x m) next to the actual total."""
    f = frame.assign(_exp=frame[p_col] * frame[m_col], _act=frame["minutes"].fillna(0.0))
    return f.groupby(["game_id", "team_id"]).agg(expected=("_exp", "sum"), actual=("_act", "sum"), listed=("player_id", "size"))
