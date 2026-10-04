"""Overtime and the team-minutes budget.

Overtime periods are counted from the box score itself (the team's summed
minutes: ~240 in regulation, +25 per overtime period; ESPN rounds each
player to whole minutes, so totals are noisy by a minute or two) - an
OUTCOME, used only as a label. The pregame estimate of overtime uses only
pregame information: the long-run rate, optionally by how evenly matched the
teams were (|team net - opponent net|, both read as of the cutoff).

Expected budget = 240 + 25 x E[overtime periods].
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from app.nba.rotation.allocation import OVERTIME_TEAM_MINUTES, REGULATION_TEAM_MINUTES


def overtime_periods(listings: pd.DataFrame) -> pd.Series:
    """Overtime periods per game, from the larger of the two teams' summed
    minutes (indexed by game_id)."""
    team = listings.groupby(["game_id", "team_id"])["minutes"].sum()
    game = team.groupby("game_id").max()
    return ((game - REGULATION_TEAM_MINUTES) / OVERTIME_TEAM_MINUTES).round().clip(lower=0).astype(int)


class OvertimeModel:
    """P(overtime) and E[overtime periods | overtime], fitted on training games."""

    def __init__(self, use_matchup: bool):
        self.use_matchup = use_matchup

    def fit(self, games: pd.DataFrame) -> "OvertimeModel":
        y = (games["ot_periods"] > 0).astype(int).to_numpy()
        self.rate_ = float(y.mean())
        self.periods_if_ot_ = float(games.loc[games["ot_periods"] > 0, "ot_periods"].mean()) if y.any() else 1.0
        if self.use_matchup:
            self.model_ = LogisticRegression(max_iter=1000).fit(games[["abs_net_diff"]].to_numpy(), y)
        return self

    def p_overtime(self, games: pd.DataFrame) -> np.ndarray:
        if self.use_matchup:
            return self.model_.predict_proba(games[["abs_net_diff"]].to_numpy())[:, 1]
        return np.full(len(games), self.rate_)

    def budget(self, games: pd.DataFrame) -> np.ndarray:
        return REGULATION_TEAM_MINUTES + OVERTIME_TEAM_MINUTES * self.periods_if_ot_ * self.p_overtime(games)
