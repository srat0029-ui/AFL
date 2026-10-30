"""Leakage-safe stat history for each row of the Minutes V1 dataset.

The rows and their cutoff (`t_cut`, tip-off minus the result lag) come from
app/nba/minutes/features.py. Each history column is a cumulative statistic
that includes only that history row and the rows before it, read with the same
as-of join, so nothing from the target game or later can reach a feature.

Only games the player actually played feed a rate or an average: a DNP is not
a zero.
"""

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nba import COMPETITIVE_SEASON_TYPES, NbaGame, NbaPlayerGameLog
from app.nba.minutes.features import _asof, _final_competitive_games

STATS = ("points", "rebounds", "assists")
DIRECT_HALFLIVES = (4, 10)
RATE_HALFLIVES = (10, 25, 50)
RECENT_MINUTES_HALFLIFE = 4
LAST_N = 10


def load_stat_logs(db: Session, *, min_season: int, max_season: int) -> pd.DataFrame:
    """Played and did-not-play box-score rows with the three stats."""
    stmt = (
        select(
            NbaPlayerGameLog.player_id,
            NbaPlayerGameLog.game_id,
            NbaPlayerGameLog.did_not_play,
            NbaPlayerGameLog.minutes,
            NbaPlayerGameLog.points,
            NbaPlayerGameLog.rebounds,
            NbaPlayerGameLog.assists,
        )
        .join(NbaGame, NbaGame.id == NbaPlayerGameLog.game_id)
        .where(NbaGame.season_start_year >= min_season, NbaGame.season_start_year <= max_season, NbaGame.season_type.in_(COMPETITIVE_SEASON_TYPES))
    )
    logs = pd.DataFrame(db.execute(stmt).all(), columns=["player_id", "game_id", "did_not_play", "minutes", *STATS])
    logs["did_not_play"] = logs["did_not_play"].astype(bool)
    for col in ("minutes", *STATS):
        logs[col] = logs[col].astype("float64")
    return logs


def _stat_history(stat_logs: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    final = _final_competitive_games(games)[["game_id", "season_start_year", "scheduled_start"]]
    rows = stat_logs.merge(final, on="game_id", how="inner")
    played = rows[~rows["did_not_play"] & rows["minutes"].notna()]
    played = played.sort_values(["player_id", "scheduled_start", "game_id"]).reset_index(drop=True)
    by_player = played.groupby("player_id", sort=False)
    new: dict[str, pd.Series | np.ndarray] = {"season_start_year": played["season_start_year"], "scheduled_start": played["scheduled_start"], "player_id": played["player_id"]}
    games_so_far = by_player.cumcount() + 1.0

    for stat in STATS:
        grouped = by_player[stat]
        new[f"{stat}_last_n"] = grouped.transform(lambda s: s.rolling(LAST_N, min_periods=1).mean())
        new[f"{stat}_season"] = played.groupby(["player_id", "season_start_year"], sort=False)[stat].transform(lambda s: s.expanding().mean())
        for h in sorted({*DIRECT_HALFLIVES, *RATE_HALFLIVES}):
            new[f"{stat}_ewm{h}"] = grouped.transform(lambda s, h=h: s.ewm(halflife=h).mean())
    for h in sorted({*RATE_HALFLIVES, RECENT_MINUTES_HALFLIFE}):
        new[f"min_ewm{h}"] = by_player["minutes"].transform(lambda s, h=h: s.ewm(halflife=h).mean())
        # Total weight of the decayed average, so mean x weight is a decayed sum.
        decay = 0.5 ** (1.0 / h)
        new[f"weight{h}"] = (1.0 - decay**games_so_far) / (1.0 - decay)
    return pd.DataFrame(new)


def add_stat_history(data: pd.DataFrame, stat_logs: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """`data` (from build_historical_dataset) plus history columns as of each
    row's own cutoff, and the row's outcomes under `actual_<stat>`."""
    history = _stat_history(stat_logs, games)
    columns = [c for c in history.columns if c not in ("player_id", "scheduled_start", "season_start_year")]
    joined = _asof(data, history.assign(hist_season=history["season_start_year"]), "player_id", [*columns, "hist_season"])
    out = data.copy()
    for col in columns:
        out[col] = joined[col].to_numpy()
    # A new season restarts the season average, as in the minutes features.
    stale = joined["hist_season"].to_numpy() < data["season_start_year"].to_numpy()
    for stat in STATS:
        out[f"{stat}_season"] = out[f"{stat}_season"].where(~stale)

    outcomes = stat_logs[["player_id", "game_id", *STATS]].rename(columns={s: f"actual_{s}" for s in STATS})
    merged = out[["player_id", "game_id"]].merge(outcomes, on=["player_id", "game_id"], how="left", validate="one_to_one")
    for stat in STATS:
        out[f"actual_{stat}"] = merged[f"actual_{stat}"].to_numpy()
    return out
