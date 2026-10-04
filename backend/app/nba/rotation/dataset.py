"""The historical participation datasets - two candidate universes.

"roster" (PRIMARY, V1.5): the pregame roster reconstructed from history
alone (app/nba/rotation/universe.py): a player is a candidate for team T's
game if his latest box-score listing known at the cutoff was for T, this
season or last. Injured / inactive players stay in (and do not play).
This is the universe a pregame system without availability evidence faces.

"listed" (SCENARIO ONLY): the players listed in game Y's box score - in
practice the official active list (about 13 per team), which is published
~30 minutes before tip-off. Conditioning on it means knowing who is
inactive, i.e. availability evidence; it is evaluated only as a labelled
"active list known" scenario (the kind of information Minutes V2 will add),
never used for V1.5 serving.

Labels (outcomes - never features):
- y_play:   entered the game. Not listed, or listed did-not-play => 0. A
            listed player who played with minutes unrecorded counts as played.
- y_rot5:   played >= 5 minutes   (NaN if he played with minutes unrecorded)
- y_rot10:  played >= 10 minutes  (same) - the "meaningful rotation" target
- listed:   dressed (on the active list) - roster universe only
"""

import numpy as np
import pandas as pd

from app.nba.minutes.dataset import DEFAULT_FIRST_SEASON, DEFAULT_LAST_SEASON, build_historical_dataset, history_logs
from app.nba.minutes.features import build_features
from app.nba.rotation.features import CAMEO_MINUTES, ROTATION_MINUTES, add_participation_features
from app.nba.rotation.universe import attach_outcomes, reconstructed_roster

UNIVERSE_ROSTER = "roster"
UNIVERSE_LISTED = "listed"


def _threshold_label(minutes_or_zero: pd.Series, threshold: float) -> pd.Series:
    return pd.Series(np.where(minutes_or_zero.isna(), np.nan, (minutes_or_zero >= threshold).astype(float)), index=minutes_or_zero.index)


def build_rotation_dataset(
    games: pd.DataFrame, logs: pd.DataFrame, *, universe: str = UNIVERSE_ROSTER, first_season: int = DEFAULT_FIRST_SEASON, last_season: int = DEFAULT_LAST_SEASON
) -> pd.DataFrame:
    hist = history_logs(logs, games, first_season, last_season)
    if universe == UNIVERSE_LISTED:
        data = build_historical_dataset(games, hist, first_season=first_season, last_season=last_season)
        data["y_play"] = (~data["did_not_play"].astype(bool)).astype(float)
        data["listed"] = True
    elif universe == UNIVERSE_ROSTER:
        cands = attach_outcomes(reconstructed_roster(games, hist, first_season=first_season, last_season=last_season), hist)
        data = build_features(games, hist, cands[["player_id", "game_id", "team_id"]])
        for col in ("listed", "y_play", "minutes", "minutes_or_zero"):
            data[col] = cands[col].to_numpy()
        data["played"] = (data["y_play"] == 1.0) & data["minutes"].notna()
        data["did_not_play"] = data["y_play"] == 0.0
    else:
        raise ValueError(f"unknown universe {universe!r}")
    data = add_participation_features(data, games, hist)
    data["y_rot5"] = _threshold_label(data["minutes_or_zero"], CAMEO_MINUTES)
    data["y_rot10"] = _threshold_label(data["minutes_or_zero"], ROTATION_MINUTES)
    data["universe"] = universe
    return data
