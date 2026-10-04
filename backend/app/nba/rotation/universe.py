"""The pregame candidate universe, reconstructed from history alone.

Why this exists: an ESPN box score lists the players who DRESSED for the
game (about 13 per team) - effectively the official active list, which is
published about 30 minutes before tip-off together with the inactive list.
Most injured players are simply not listed (only ~0.35 injury/rest-type DNP
listings per team-game). Using "listed in game Y's box score" as the
candidate set would therefore smuggle availability information - who was
inactive - into a model that is supposed to use none.

The historically reconstructable equivalent of "on the team's roster before
the game" is:

    player P is a candidate for team T's game Y if P's LATEST box-score
    listing known at Y's cutoff was for T, in Y's season or the season before.

A player who is injured stays a candidate (and simply does not play), a
player who appears for another team leaves, and a player's first ever
listing for a team (a debut, a signing, or a trade not yet visible in any box
score) is NOT a candidate - historically there is no way to have known.
Those "unforeseen" listings are counted and reported; prospectively the
roster observation covers them.

The same cutoff rule as everywhere else: a listing is known only if its game
was final and tipped off at least GAME_RESULT_AVAILABILITY_LAG before the
cutoff.
"""

import numpy as np
import pandas as pd

from app.models.nba import COMPETITIVE_SEASON_TYPES
from app.nba.minutes.features import LAG


def reconstructed_roster(games: pd.DataFrame, logs: pd.DataFrame, *, first_season: int, last_season: int) -> pd.DataFrame:
    """Candidates (player_id, game_id, team_id) for every final competitive
    game of `team_id` in [first_season, last_season]."""
    final = games[(games["status"] == "final") & games["season_type"].isin(COMPETITIVE_SEASON_TYPES)]
    target_games = final[final["season_start_year"].between(first_season, last_season)]
    listings = logs.merge(final[["game_id", "scheduled_start", "season_start_year"]], on="game_id", how="inner")
    listings = listings.sort_values(["scheduled_start", "game_id"])[["player_id", "team_id", "scheduled_start", "season_start_year"]]

    # team-games, one row per (game, team)
    tg = pd.concat([
        target_games[["game_id", "scheduled_start", "season_start_year", "home_team_id"]].rename(columns={"home_team_id": "team_id"}),
        target_games[["game_id", "scheduled_start", "season_start_year", "away_team_id"]].rename(columns={"away_team_id": "team_id"}),
    ], ignore_index=True)
    tg["t_cut"] = tg["scheduled_start"] - LAG

    out = []
    for season, games_s in tg.groupby("season_start_year"):
        # players ever listed in this season or the previous one: the only possible candidates
        pool = listings[listings["season_start_year"].between(season - 1, season)]
        players = pool["player_id"].unique()
        # latest listing per player as of each team-game cutoff
        left = games_s.assign(_k=1).merge(pd.DataFrame({"player_id": players, "_k": 1}), on="_k").drop(columns="_k")
        left = left.sort_values("t_cut")
        right = pool.rename(columns={"team_id": "last_team_id", "scheduled_start": "last_listed_at", "season_start_year": "last_season"}).sort_values("last_listed_at")
        merged = pd.merge_asof(left, right, left_on="t_cut", right_on="last_listed_at", by="player_id", direction="backward", allow_exact_matches=True)
        keep = merged["last_team_id"].notna() & (merged["last_team_id"] == merged["team_id"]) & (merged["last_season"] >= season - 1)
        out.append(merged.loc[keep, ["player_id", "game_id", "team_id"]])
    roster = pd.concat(out, ignore_index=True).astype({"player_id": int, "game_id": int, "team_id": int})
    return roster.drop_duplicates(["player_id", "game_id"]).reset_index(drop=True)


def attach_outcomes(candidates: pd.DataFrame, logs: pd.DataFrame) -> pd.DataFrame:
    """Labels for candidates (outcomes only): listed (dressed), played,
    minutes. Not listed => did not play."""
    lg = logs[["player_id", "game_id", "team_id", "did_not_play", "minutes"]].rename(columns={"team_id": "listed_team_id"})
    out = candidates.merge(lg, on=["player_id", "game_id"], how="left")
    out["listed"] = out["listed_team_id"].notna() & (out["listed_team_id"] == out["team_id"])
    played = out["listed"] & (out["did_not_play"] == False)  # noqa: E712 - NaN-safe comparison
    out["y_play"] = played.astype(float)
    out["minutes_or_zero"] = np.where(played, out["minutes"], 0.0)
    out["minutes"] = np.where(played, out["minutes"], np.nan)
    return out.drop(columns=["listed_team_id"])
