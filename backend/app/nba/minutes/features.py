"""The leakage-safe feature builder for the expected-minutes model.

Every row is "player P before game G". Its features come from history that
was known at the row's cutoff and from nothing else:

    t_cut = min(G's tip-off, prediction cutoff) - GAME_RESULT_AVAILABILITY_LAG

A box score contributes only if its game is final and tipped off at or
before t_cut - the same rule as app/nba/asof.py's game_logs_known_at, so a
backtest row and a live prediction read history identically. The rule is
applied with as-of joins (pandas.merge_asof, "latest history row with
tip-off <= t_cut"), and each history-side feature is a cumulative statistic
that includes only that history row and the rows before it. So a feature
can never see the target game or anything after it, and that holds for
every row whether it is a historical training row or an upcoming game.

What is deliberately NOT used:
- anything from game G's own box score (minutes, starter flag, DNP flag,
  who else was listed);
- injury reports or did-not-play REASONS: there are no trustworthy
  historical pregame injury snapshots, so V1 stays injury-free and V2 will
  add the prospective evidence;
- the schedule beyond game G (only earlier games feed rest/density), and
  scores of games that were not final by t_cut.

Schedule context (rest, back-to-backs, density, season progress) is computed
from nba_games, not from player appearances, so a game whose box score is
missing still counts as a game played.
"""

import numpy as np
import pandas as pd

from app.models.nba import COMPETITIVE_SEASON_TYPES
from app.nba.asof import GAME_RESULT_AVAILABILITY_LAG

LAG = pd.Timedelta(GAME_RESULT_AVAILABILITY_LAG)
EWM_HALFLIFE_GAMES = 4.0
ROTATION_MINUTES = 10.0  # a "rotation player" in a game played at least this many minutes
TEAM_NET_SHRINK_GAMES = 5.0
VACATED_RECENT_WINDOW = pd.Timedelta(days=14)
DAYS_SINCE_PLAYED_CAP = 30
TEAM_GAMES_MISSED_CAP = 20.0  # season point differential shrunk towards 0 by this many games
SCHEDULE_STATUSES = ("final", "scheduled", "in_progress")  # postponed / cancelled games are not games played

PLAYER_FEATURES = [
    "min_last1",
    "min_mean3",
    "min_mean5",
    "min_mean10",
    "min_median10",
    "min_std10",
    "min_ewm",
    "min_max5",
    "min_min5",
    "career_games",
    "season_mean",
    "season_games",
    "prev_season_mean",
    "days_since_played",
    "team_games_missed",
]
ROLE_FEATURES = [
    "started_last",
    "start_rate5",
    "start_rate10",
    "games_since_start",
    "dnp_last_listed",
    "dnp_rate10",
    "played_streak",
]
TEAM_CONTEXT_FEATURES = [
    "share_mean5",
    "rank_mean5",
    "games_with_team",
    "team_min_mean5",
    "traded",
    "team_rotation5",
    "team_vacated_last",
    "last_margin",
]
SCHEDULE_FEATURES = [
    "is_home",
    "rest_days",
    "back_to_back",
    "games_prev_7d",
    "opp_rest_days",
    "opp_back_to_back",
    "is_playoffs",
    "is_play_in",
    "season_progress",
    "team_net",
    "opp_net",
    "abs_net_diff",
]
FEATURE_GROUPS = {
    "player_minutes": PLAYER_FEATURES,
    "role": ROLE_FEATURES,
    "team_context": TEAM_CONTEXT_FEATURES,
    "schedule": SCHEDULE_FEATURES,
}
ALL_FEATURES = PLAYER_FEATURES + ROLE_FEATURES + TEAM_CONTEXT_FEATURES + SCHEDULE_FEATURES


def _rolling(frame: pd.DataFrame, by, column: str, window: int, how: str, min_periods: int = 1) -> pd.Series:
    """Per-group rolling statistic over rows in their current order,
    INCLUDING the row itself (history rows only - the as-of join is what
    keeps it strictly before the target)."""
    return frame.groupby(by, sort=False)[column].transform(lambda s: getattr(s.rolling(window, min_periods=min_periods), how)())


def _final_competitive_games(games: pd.DataFrame) -> pd.DataFrame:
    return games[(games["status"] == "final") & games["season_type"].isin(COMPETITIVE_SEASON_TYPES)]


def _team_game_long(games: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, team) from the schedule, with the opponent."""
    base = ["game_id", "season_start_year", "season_type", "game_date", "scheduled_start", "status", "home_score", "away_score"]
    home = games[base + ["home_team_id", "away_team_id"]].rename(columns={"home_team_id": "team_id", "away_team_id": "opp_id"})
    home["is_home"] = 1.0
    away = games[base + ["away_team_id", "home_team_id"]].rename(columns={"away_team_id": "team_id", "home_team_id": "opp_id"})
    away["is_home"] = 0.0
    long = pd.concat([home, away], ignore_index=True)
    long["team_score"] = np.where(long["is_home"] == 1.0, long["home_score"], long["away_score"])
    long["opp_score"] = np.where(long["is_home"] == 1.0, long["away_score"], long["home_score"])
    return long.drop(columns=["home_score", "away_score"])


def schedule_context(games: pd.DataFrame) -> pd.DataFrame:
    """Rest and schedule density per (game, team), from the schedule alone.

    For each game only the team's EARLIER games are read, and only their
    dates - the schedule is published in advance, so this is pregame
    knowledge even for games far in the future. Postponed and cancelled
    games are not counted as games played."""
    sched = games[games["season_type"].isin(COMPETITIVE_SEASON_TYPES) & games["status"].isin(SCHEDULE_STATUSES)]
    long = _team_game_long(sched).sort_values(["team_id", "scheduled_start", "game_id"]).reset_index(drop=True)
    grouped = long.groupby("team_id", sort=False)
    prev_date = grouped["game_date"].shift(1)
    prev_season = grouped["season_start_year"].shift(1)
    rest = (long["game_date"] - prev_date).dt.days
    # Season opener (no earlier game this season): treat as fully rested.
    rest = rest.where(prev_season == long["season_start_year"], np.nan)
    long["rest_days"] = rest.fillna(7.0).clip(upper=7.0)
    long["back_to_back"] = (long["rest_days"] == 1.0).astype(float)

    counts = np.zeros(len(long))
    for _, idx in long.groupby("team_id", sort=False).indices.items():
        dates = long["game_date"].to_numpy()[idx].astype("datetime64[D]")
        lower = np.searchsorted(dates, dates - np.timedelta64(7, "D"), side="left")
        counts[idx] = np.arange(len(idx)) - lower  # earlier games within the previous 7 days
    long["games_prev_7d"] = counts

    regular = long["season_type"] == "regular"
    reg_before = long.assign(_r=regular.astype(int)).groupby(["team_id", "season_start_year"], sort=False)["_r"].cumsum() - regular.astype(int)
    long["season_progress"] = np.where(regular, (reg_before / 82.0).clip(upper=1.0), 1.0)
    return long[["game_id", "team_id", "opp_id", "is_home", "rest_days", "back_to_back", "games_prev_7d", "season_progress"]]


def _team_strength_history(games: pd.DataFrame) -> pd.DataFrame:
    """Per team, after each FINAL game: cumulative point differential and
    game count for that season (the as-of join picks the latest one known)."""
    long = _team_game_long(_final_competitive_games(games)).dropna(subset=["team_score", "opp_score"])
    long = long.sort_values(["team_id", "scheduled_start", "game_id"]).reset_index(drop=True)
    long["margin"] = long["team_score"] - long["opp_score"]
    g = long.groupby(["team_id", "season_start_year"], sort=False)
    long["net_sum"] = g["margin"].cumsum()
    long["net_n"] = g.cumcount() + 1
    return long[["team_id", "scheduled_start", "season_start_year", "net_sum", "net_n"]]


def _asof(left: pd.DataFrame, right: pd.DataFrame, by, right_cols: list[str], prefix: str = "") -> pd.DataFrame:
    """For each left row, the latest right row with scheduled_start <= t_cut
    (within the same `by` group). Returns right_cols aligned to left's index."""
    by = [by] if isinstance(by, str) else list(by)
    lhs = left[by + ["t_cut"]].copy()
    lhs["_row"] = left.index
    lhs = lhs.sort_values("t_cut")
    r = right[by + ["scheduled_start"] + right_cols].rename(columns={"scheduled_start": "_hist_start"}).sort_values("_hist_start")
    merged = pd.merge_asof(lhs, r, left_on="t_cut", right_on="_hist_start", by=by, direction="backward", allow_exact_matches=True)
    if (merged["_hist_start"] > merged["t_cut"]).any():  # pragma: no cover - merge_asof guarantees this
        raise AssertionError("as-of join returned history after the cutoff")
    merged = merged.set_index("_row").reindex(left.index)
    out = merged[right_cols + ["_hist_start"]]
    return out.add_prefix(prefix) if prefix else out


def _player_history(logs: pd.DataFrame, games: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cumulative per-player statistics after each known box-score row.

    Returns (played, listed): `played` has one row per game the player
    actually played with minutes recorded; `listed` has one row per box
    score he was listed in (including did-not-play rows)."""
    final = _final_competitive_games(games)[["game_id", "season_start_year", "game_date", "scheduled_start", "home_team_id", "home_score", "away_score"]]
    rows = logs.merge(final, on="game_id", how="inner")
    rows = rows.sort_values(["player_id", "scheduled_start", "game_id"]).reset_index(drop=True)

    listed = rows[["player_id", "game_id", "team_id", "scheduled_start", "did_not_play"]].copy()
    listed["dnp"] = listed["did_not_play"].astype(float)
    listed["dnp_rate10"] = _rolling(listed, "player_id", "dnp", 10, "mean")
    block = listed.groupby("player_id", sort=False)["dnp"].cumsum()
    listed["played_streak"] = (1.0 - listed["dnp"]).groupby([listed["player_id"], block], sort=False).cumsum()

    played = rows[(~rows["did_not_play"]) & rows["minutes"].notna()].copy().reset_index(drop=True)
    team_total = played.groupby(["game_id", "team_id"])["minutes"].transform("sum")
    played["share"] = np.where(team_total > 0, played["minutes"] / team_total, np.nan)
    played["rank"] = played.groupby(["game_id", "team_id"])["minutes"].rank(ascending=False, method="min")
    is_home = played["team_id"] == played["home_team_id"]
    played["margin"] = np.where(is_home, played["home_score"] - played["away_score"], played["away_score"] - played["home_score"])

    p = "player_id"
    played["min_last1"] = played["minutes"]
    for w in (3, 5, 10):
        played[f"min_mean{w}"] = _rolling(played, p, "minutes", w, "mean")
    played["min_median10"] = _rolling(played, p, "minutes", 10, "median")
    played["min_std10"] = _rolling(played, p, "minutes", 10, "std", min_periods=3)
    played["min_max5"] = _rolling(played, p, "minutes", 5, "max")
    played["min_min5"] = _rolling(played, p, "minutes", 5, "min")
    played["min_ewm"] = played.groupby(p, sort=False)["minutes"].transform(lambda s: s.ewm(halflife=EWM_HALFLIFE_GAMES).mean())
    played["career_games"] = played.groupby(p, sort=False).cumcount() + 1.0
    season_group = played.groupby([p, "season_start_year"], sort=False)["minutes"]
    played["season_mean"] = season_group.transform(lambda s: s.expanding().mean())
    played["season_games"] = played.groupby([p, "season_start_year"], sort=False).cumcount() + 1.0
    full_season = played.groupby([p, "season_start_year"])["minutes"].mean()
    played["prev_season_mean"] = pd.Series(
        full_season.reindex(pd.MultiIndex.from_arrays([played[p], played["season_start_year"] - 1])).to_numpy(), index=played.index
    )
    played["started_last"] = played["started"]
    played["start_rate5"] = _rolling(played, p, "started", 5, "mean")
    played["start_rate10"] = _rolling(played, p, "started", 10, "mean")
    idx = played.groupby(p, sort=False).cumcount().astype(float)
    last_start = idx.where(played["started"] == 1.0).groupby(played[p], sort=False).ffill()
    played["games_since_start"] = (idx - last_start).fillna(np.minimum(idx + 1.0, 100.0)).clip(upper=100.0)
    played["share_mean5"] = _rolling(played, p, "share", 5, "mean")
    played["rank_mean5"] = _rolling(played, p, "rank", 5, "mean")
    played["last_margin"] = played["margin"]
    played["last_team_id"] = played["team_id"]
    played["last_game_date"] = played["game_date"]

    pair = [p, "team_id"]
    played["games_with_team"] = played.groupby(pair, sort=False).cumcount() + 1.0
    played["team_min_mean5"] = _rolling(played, pair, "minutes", 5, "mean")
    return played, listed


HISTORY_COLUMNS = [
    "min_last1",
    "min_mean3",
    "min_mean5",
    "min_mean10",
    "min_median10",
    "min_std10",
    "min_max5",
    "min_min5",
    "min_ewm",
    "career_games",
    "season_mean",
    "season_games",
    "prev_season_mean",
    "season_start_year",
    "started_last",
    "start_rate5",
    "start_rate10",
    "games_since_start",
    "share_mean5",
    "rank_mean5",
    "last_margin",
    "last_team_id",
    "last_game_date",
]


def _team_rotation_history(played: pd.DataFrame, listed: pd.DataFrame) -> pd.DataFrame:
    """Per team, after each box-scored game: recent rotation size, and the
    minutes "vacated" by listed teammates who did not play although they had
    played within VACATED_RECENT_WINDOW (each one's pre-game 10-game minutes
    average, itself read as of that game). Derived from box scores only -
    whether the absence continues into the next game is NOT known here."""
    per_game = played.groupby(["team_id", "game_id"]).agg(
        scheduled_start=("scheduled_start", "first"), rotation=("minutes", lambda m: float((m >= ROTATION_MINUTES).sum()))
    )
    per_game = per_game.reset_index().sort_values(["team_id", "scheduled_start", "game_id"]).reset_index(drop=True)
    per_game["team_rotation5"] = _rolling(per_game, "team_id", "rotation", 5, "mean")

    dnp_rows = listed[listed["did_not_play"]].copy()
    dnp_rows["t_cut"] = dnp_rows["scheduled_start"] - LAG
    pre = _asof(dnp_rows, played, "player_id", ["min_mean10"])
    # Only teammates who had been playing recently: a player out for months
    # is already absorbed into everyone else's recent minutes.
    recent = (dnp_rows["scheduled_start"] - pre["_hist_start"]) <= VACATED_RECENT_WINDOW
    dnp_rows["vacated"] = pre["min_mean10"].where(recent, 0.0).fillna(0.0)
    vacated = dnp_rows.groupby(["team_id", "game_id"])["vacated"].sum()
    per_game["team_vacated"] = vacated.reindex(pd.MultiIndex.from_frame(per_game[["team_id", "game_id"]])).fillna(0.0).to_numpy()
    return per_game, dnp_rows[["player_id", "team_id", "game_id", "vacated"]]


def build_features(
    games: pd.DataFrame,
    logs: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    cutoff: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Features for each target row (player_id, game_id, team_id).

    `cutoff` (naive UTC) is the prediction time for prospective rows; for
    historical rows leave it None and each row's cutoff is its own tip-off.
    Returns the targets with ALL_FEATURES plus bookkeeping columns
    (`t_cut`, `history_last_start`, `eligible`, `last_team_id`, ...)."""
    out = targets.reset_index(drop=True).copy()
    info = games[["game_id", "season_start_year", "season_type", "game_date", "scheduled_start"]]
    out = out.merge(info, on="game_id", how="left", validate="many_to_one")
    if out["scheduled_start"].isna().any():
        raise ValueError("every target game must be in the schedule")
    start = out["scheduled_start"]
    if cutoff is not None:
        start = start.where(start <= cutoff, cutoff)
    out["t_cut"] = start - LAG

    played, listed = _player_history(logs, games)

    hist = _asof(out, played, "player_id", HISTORY_COLUMNS, prefix="h_")
    for col in HISTORY_COLUMNS:
        if col != "season_start_year":  # the target row keeps its own season
            out[col] = hist[f"h_{col}"].to_numpy()
    out["history_last_start"] = hist["h__hist_start"].to_numpy()
    # A new season: season-to-date stats restart; last season's full mean
    # becomes "previous season"; anything older is not carried forward.
    hist_season = hist["h_season_start_year"].to_numpy()
    target_season = out["season_start_year"].to_numpy()
    new_season = hist_season < target_season
    out["prev_season_mean"] = np.where(new_season, np.where(hist_season == target_season - 1, out["season_mean"], np.nan), out["prev_season_mean"])
    out["season_mean"] = np.where(new_season, np.nan, out["season_mean"])
    out["season_games"] = np.where(new_season, 0.0, out["season_games"])
    # Games since the modelled window began, not a true career count - capped
    # so it means "up to a season of known history" and does not keep growing
    # with calendar time (which a linear model would extrapolate).
    out["career_games"] = out["career_games"].fillna(0.0).clip(upper=82.0)
    out["eligible"] = out["min_last1"].notna()

    with_team = _asof(out, played, ["player_id", "team_id"], ["games_with_team", "team_min_mean5"])
    out["games_with_team"] = with_team["games_with_team"].fillna(0.0).clip(upper=82.0).to_numpy()
    out["team_min_mean5"] = with_team["team_min_mean5"].to_numpy()
    out["traded"] = ((out["last_team_id"].notna()) & (out["last_team_id"] != out["team_id"])).astype(float)

    lst = _asof(out, listed, "player_id", ["dnp", "dnp_rate10", "played_streak"])
    out["dnp_last_listed"] = lst["dnp"].to_numpy()
    out["dnp_rate10"] = lst["dnp_rate10"].to_numpy()
    out["played_streak"] = lst["played_streak"].clip(upper=82.0).to_numpy()

    # Capped: beyond a month the exact gap carries little and would let a
    # linear model extrapolate wildly on an off-season gap (which
    # season_games == 0 already marks).
    out["days_since_played"] = (out["game_date"] - out["last_game_date"]).dt.days.clip(upper=DAYS_SINCE_PLAYED_CAP).astype(float)
    out["team_games_missed"] = np.minimum(_team_games_between(games, out), TEAM_GAMES_MISSED_CAP)

    rotation, dnp_contrib = _team_rotation_history(played, listed)
    rot = _asof(out, rotation, "team_id", ["game_id", "team_rotation5", "team_vacated"], prefix="r_")
    out["team_rotation5"] = rot["r_team_rotation5"].to_numpy()
    # Exclude the player's own absence from "minutes vacated by teammates".
    own = pd.DataFrame({"player_id": out["player_id"], "team_id": out["team_id"], "game_id": rot["r_game_id"].to_numpy()})
    own = own.merge(dnp_contrib, on=["player_id", "team_id", "game_id"], how="left")
    out["team_vacated_last"] = (rot["r_team_vacated"].fillna(0.0).to_numpy() - own["vacated"].fillna(0.0).to_numpy()).clip(min=0.0)

    sched = schedule_context(games)
    out = out.merge(sched, on=["game_id", "team_id"], how="left", validate="many_to_one")
    opp = sched[["game_id", "team_id", "rest_days", "back_to_back"]].rename(
        columns={"team_id": "opp_id", "rest_days": "opp_rest_days", "back_to_back": "opp_back_to_back"}
    )
    out = out.merge(opp, on=["game_id", "opp_id"], how="left", validate="many_to_one")
    out["is_playoffs"] = (out["season_type"] == "playoffs").astype(float)
    out["is_play_in"] = (out["season_type"] == "play_in").astype(float)

    strength = _team_strength_history(games)
    for side, team_col in (("team_net", "team_id"), ("opp_net", "opp_id")):
        left = out[[team_col, "t_cut"]].rename(columns={team_col: "team_id"})
        s = _asof(left, strength, "team_id", ["season_start_year", "net_sum", "net_n"])
        same = s["season_start_year"].to_numpy() == out["season_start_year"].to_numpy()
        out[side] = np.where(same, s["net_sum"].to_numpy() / (s["net_n"].to_numpy() + TEAM_NET_SHRINK_GAMES), 0.0)
    out["abs_net_diff"] = (out["team_net"] - out["opp_net"]).abs()
    return out


def _team_games_between(games: pd.DataFrame, out: pd.DataFrame) -> np.ndarray:
    """How many games the player's (current) team played strictly between
    the player's last played game and this one - i.e. games he missed. Read
    from the schedule, so a game with a missing box score still counts."""
    sched = games[games["season_type"].isin(COMPETITIVE_SEASON_TYPES) & games["status"].isin(SCHEDULE_STATUSES)]
    long = _team_game_long(sched)
    result = np.full(len(out), np.nan)
    starts_by_team = {team: np.sort(g["scheduled_start"].to_numpy()) for team, g in long.groupby("team_id")}
    last = out["history_last_start"].to_numpy()
    target = out["scheduled_start"].to_numpy()
    for team, idx in out.groupby("team_id").indices.items():
        starts = starts_by_team.get(team)
        if starts is None:
            continue
        has = ~pd.isna(last[idx])
        sel = idx[has]
        hi = np.searchsorted(starts, target[sel], side="left")
        lo = np.searchsorted(starts, last[sel], side="right")
        result[sel] = np.maximum(hi - lo, 0)
    return np.minimum(result, 82.0)


def truncate_to_cutoff(games: pd.DataFrame, logs: pd.DataFrame, t_cut: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The world as it was known at `t_cut`: box scores of games that tipped
    off after it are removed, and those games' results (status, scores) are
    reset to an unplayed schedule entry. The schedule itself stays - it is
    published in advance. Used to prove the feature builder is leakage-free:
    features computed from the full data must equal features computed from
    this truncated view."""
    games = games.copy()
    future = games["scheduled_start"] > t_cut
    games.loc[future & (games["status"] == "final"), "status"] = "scheduled"
    games.loc[future, ["home_score", "away_score"]] = np.nan
    games.loc[future, "box_score_state"] = None
    future_ids = set(games.loc[future, "game_id"])
    return games, logs[~logs["game_id"].isin(future_ids)]


def lookahead_violations(games: pd.DataFrame, logs: pd.DataFrame, targets: pd.DataFrame, *, atol: float = 1e-9) -> list[dict]:
    """Recompute each target row's features from data truncated at that row's
    own cutoff and compare with the features computed from the full data.
    Any difference means a feature used information it could not have had.
    Returns the differing (row, feature) pairs; empty means clean."""
    full = build_features(games, logs, targets)
    problems: list[dict] = []
    for i, row in full.iterrows():
        g_t, l_t = truncate_to_cutoff(games, logs, row["t_cut"])
        # the target row's own game stays in the schedule, unplayed
        one = build_features(g_t, l_t, targets.iloc[[i]][["player_id", "game_id", "team_id"]])
        for col in ALL_FEATURES:
            a, b = row[col], one.iloc[0][col]
            if pd.isna(a) and pd.isna(b):
                continue
            if pd.isna(a) != pd.isna(b) or abs(float(a) - float(b)) > atol:
                problems.append({"row": int(i), "player_id": int(row["player_id"]), "game_id": int(row["game_id"]), "feature": col, "full": a, "truncated": b})
    return problems
