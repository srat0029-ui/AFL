"""The historical modelling dataset and its eligibility rules.

Seasons: 2018-19 through 2025-26 by default (season_start_year 2018..2025).
2015-16..2017-18 stay in the database but are excluded - ESPN has no usable
box score for many New Orleans and Chicago games in those seasons, so player
histories built from them would be structurally incomplete. They are not
used even as feature lookback, so the first modelled season starts with no
prior history (2018-19 rows therefore have lower coverage, and 2018-19 is
only ever a training season). 2026-27 is the prospective season and its
outcomes are never used historically.

One row per (player, game) box-score listing in a final competitive game.

Targets:
- `minutes` (primary): minutes played, defined only when the player played
  and the source recorded his minutes. A prop is void if the player does not
  play, so "minutes given he plays" is what the stat models need.
- `minutes_or_zero` (secondary): 0 for a did-not-play listing - reported
  for completeness; predicting WHETHER he plays needs availability
  evidence V1 deliberately does not have.

Eligibility to receive a Model V1 prediction (decided only from information
before the game):
- E1: at least one earlier played game (with minutes) known at the cutoff,
  within the modelled seasons. A player with no such history - a debut, or
  in 2018-19 anyone whose history predates the window - gets no V1
  prediction; he is counted in coverage, and a documented fallback (the
  training-set median for debut rows) is reported separately.
- Nobody else is excluded: traded players, players returning from long
  absences, and players with DNP-heavy histories all stay in, and are
  broken out in the evaluation rather than dropped to flatter the metrics.
"""

import pandas as pd

from app.models.nba import COMPETITIVE_SEASON_TYPES
from app.nba.minutes.features import build_features

DEFAULT_FIRST_SEASON = 2018
DEFAULT_LAST_SEASON = 2025
PROSPECTIVE_SEASON = 2026


def history_logs(logs: pd.DataFrame, games: pd.DataFrame, first_season: int, last_season: int) -> pd.DataFrame:
    """Box scores usable as history: modelled seasons only."""
    seasons = games.set_index("game_id")["season_start_year"]
    season = logs["game_id"].map(seasons)
    return logs[(season >= first_season) & (season <= last_season)]


def build_historical_dataset(
    games: pd.DataFrame, logs: pd.DataFrame, *, first_season: int = DEFAULT_FIRST_SEASON, last_season: int = DEFAULT_LAST_SEASON
) -> pd.DataFrame:
    if last_season >= PROSPECTIVE_SEASON:
        raise ValueError(f"season {PROSPECTIVE_SEASON} is the prospective season; its outcomes are not historical training data")
    hist = history_logs(logs, games, first_season, last_season)
    final = games[(games["status"] == "final") & games["season_type"].isin(COMPETITIVE_SEASON_TYPES)]
    rows = hist[hist["game_id"].isin(final["game_id"])]
    targets = rows[["player_id", "game_id", "team_id"]]
    data = build_features(games, hist, targets)
    outcome = rows.reset_index(drop=True)
    data["did_not_play"] = outcome["did_not_play"].to_numpy()
    data["actual_started"] = outcome["started"].to_numpy()  # outcome - for analysis only, never a feature
    played = (~outcome["did_not_play"]) & outcome["minutes"].notna()
    data["played"] = played.to_numpy()
    data["minutes"] = outcome["minutes"].where(played).to_numpy()
    data["minutes_or_zero"] = outcome["minutes"].where(played, 0.0).where(played | outcome["did_not_play"]).to_numpy()
    return data
