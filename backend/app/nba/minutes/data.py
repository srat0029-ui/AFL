"""Loads the NBA schedule and box scores into pandas frames.

These are raw tables, not features: everything here is fact about a game or
a player's line in it. The information boundary is applied in features.py,
on a per-row time cutoff, so the same frames can serve every cutoff in a
backtest and the single "now" cutoff of a prospective run.

All timestamps are converted to timezone-naive UTC (`datetime64[ns]`).
"""

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nba import COMPETITIVE_SEASON_TYPES, NbaGame, NbaPlayerGameLog

GAME_COLUMNS = [
    "game_id",
    "season_start_year",
    "season_type",
    "game_date",
    "scheduled_start",
    "status",
    "home_team_id",
    "away_team_id",
    "home_score",
    "away_score",
    "box_score_state",
]

LOG_COLUMNS = ["player_id", "game_id", "team_id", "did_not_play", "started", "minutes"]


def to_utc_naive(values) -> pd.Series:
    return pd.to_datetime(values, utc=True).dt.tz_convert("UTC").dt.tz_localize(None)


def load_games(db: Session) -> pd.DataFrame:
    """Every game in the schedule, all seasons and statuses. The schedule is
    known in advance, so prior/next games are legitimate schedule context;
    scores and statuses are outcomes, and features.py only reads them for
    games that were final before a row's cutoff."""
    rows = db.execute(
        select(
            NbaGame.id,
            NbaGame.season_start_year,
            NbaGame.season_type,
            NbaGame.game_date,
            NbaGame.scheduled_start,
            NbaGame.status,
            NbaGame.home_team_id,
            NbaGame.away_team_id,
            NbaGame.home_score,
            NbaGame.away_score,
            NbaGame.box_score_state,
        )
    ).all()
    games = pd.DataFrame(rows, columns=GAME_COLUMNS)
    games["scheduled_start"] = to_utc_naive(games["scheduled_start"])
    games["game_date"] = pd.to_datetime(games["game_date"])
    return games.sort_values(["scheduled_start", "game_id"]).reset_index(drop=True)


def load_logs(db: Session, *, min_season: int, max_season: int | None = None) -> pd.DataFrame:
    """Box-score rows (including did-not-play rows) for competitive games in
    the season range. Preseason is excluded: exhibition rotations are not
    representative. Game outcome fields are not filtered by status here;
    features.py applies the final/cutoff rule."""
    stmt = (
        select(
            NbaPlayerGameLog.player_id,
            NbaPlayerGameLog.game_id,
            NbaPlayerGameLog.team_id,
            NbaPlayerGameLog.did_not_play,
            NbaPlayerGameLog.started,
            NbaPlayerGameLog.minutes,
        )
        .join(NbaGame, NbaGame.id == NbaPlayerGameLog.game_id)
        .where(NbaGame.season_start_year >= min_season, NbaGame.season_type.in_(COMPETITIVE_SEASON_TYPES))
    )
    if max_season is not None:
        stmt = stmt.where(NbaGame.season_start_year <= max_season)
    logs = pd.DataFrame(db.execute(stmt).all(), columns=LOG_COLUMNS)
    logs["did_not_play"] = logs["did_not_play"].astype(bool)
    logs["started"] = logs["started"].astype("float64")  # 1.0 / 0.0 / NaN (NaN on DNP rows)
    logs["minutes"] = logs["minutes"].astype("float64")
    return logs
