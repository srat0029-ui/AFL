"""Expected Minutes Model V1 - the feature builder and its information
boundary, on small synthetic frames (no database).

Most tests here are leakage tests: they change something the model must NOT
be able to see (the target game's own outcome, later games, a game still in
progress at the cutoff) and assert the features do not move."""

import numpy as np
import pandas as pd
import pytest

from app.nba.minutes.dataset import PROSPECTIVE_SEASON, build_historical_dataset
from app.nba.minutes.features import ALL_FEATURES, build_features, lookahead_violations, schedule_context, truncate_to_cutoff

TEAM_A, TEAM_B, TEAM_C = 1, 2, 3
P1, P2, NEW = 10, 11, 99


def _games(spec):
    """spec: list of (game_id, 'YYYY-MM-DD', home, away[, status[, season]]). Tip-off 00:00 UTC next day."""
    rows = []
    for g in spec:
        gid, day, home, away = g[:4]
        status = g[4] if len(g) > 4 else "final"
        season = g[5] if len(g) > 5 else 2023
        date = pd.Timestamp(day)
        rows.append(
            {
                "game_id": gid, "season_start_year": season, "season_type": "regular", "game_date": date,
                "scheduled_start": date + pd.Timedelta(hours=24), "status": status, "home_team_id": home, "away_team_id": away,
                "home_score": 100.0 if status == "final" else np.nan, "away_score": 95.0 if status == "final" else np.nan,
                "box_score_state": "ingested" if status == "final" else None,
            }
        )
    return pd.DataFrame(rows)


def _logs(spec):
    """spec: list of (player, game, team, minutes or 'DNP', started)."""
    rows = []
    for player, game, team, minutes, started in spec:
        dnp = minutes == "DNP"
        rows.append({"player_id": player, "game_id": game, "team_id": team, "did_not_play": dnp, "started": np.nan if dnp else float(started), "minutes": np.nan if dnp else float(minutes)})
    return pd.DataFrame(rows)


GAMES = _games([
    (1, "2023-11-01", TEAM_A, TEAM_B),
    (2, "2023-11-03", TEAM_B, TEAM_A),
    (3, "2023-11-04", TEAM_A, TEAM_B),  # back-to-back for both teams
    (4, "2023-11-07", TEAM_A, TEAM_B),
    (5, "2023-11-09", TEAM_B, TEAM_A),
    (6, "2023-11-12", TEAM_A, TEAM_B),
    (7, "2023-11-14", TEAM_C, TEAM_B),  # P1 now on team C
    (8, "2023-11-16", TEAM_B, TEAM_C),
    (9, "2023-11-18", TEAM_C, TEAM_B),
])
LOGS = _logs([
    (P1, 1, TEAM_A, 30, 1), (P1, 2, TEAM_A, 20, 0), (P1, 3, TEAM_A, "DNP", 0), (P1, 4, TEAM_A, 34, 1),
    (P1, 5, TEAM_A, 36, 1), (P1, 6, TEAM_A, 10, 0), (P1, 7, TEAM_C, 25, 1), (P1, 8, TEAM_C, 28, 1), (P1, 9, TEAM_C, 31, 1),
    (P2, 1, TEAM_B, 12, 0), (P2, 2, TEAM_B, 14, 0), (P2, 3, TEAM_B, 16, 0), (P2, 4, TEAM_B, 18, 0),
    (NEW, 9, TEAM_B, 5, 0),
])


def _features(games=GAMES, logs=LOGS, rows=None, cutoff=None):
    rows = rows if rows is not None else logs[["player_id", "game_id", "team_id"]]
    return build_features(games, logs, rows.reset_index(drop=True), cutoff=cutoff).set_index(["player_id", "game_id"])


def test_rolling_minutes_use_only_earlier_played_games():
    f = _features().loc[(P1, 6)]
    # P1 played 30, 20, (DNP), 34, 36 before game 6.
    assert f["min_last1"] == 36
    assert f["min_mean3"] == pytest.approx((20 + 34 + 36) / 3)
    assert f["min_mean5"] == pytest.approx((30 + 20 + 34 + 36) / 4)
    assert f["min_max5"] == 36 and f["min_min5"] == 20
    assert f["min_std10"] == pytest.approx(np.std([30, 20, 34, 36], ddof=1))
    assert f["career_games"] == 4
    assert f["season_mean"] == pytest.approx(30.0)


def test_first_game_has_no_history_and_is_ineligible():
    f = _features()
    assert not f.loc[(P1, 1), "eligible"] and np.isnan(f.loc[(P1, 1), "min_last1"])
    assert not f.loc[(NEW, 9), "eligible"]  # a newly appearing player gets no V1 prediction
    assert f.loc[(P1, 2), "eligible"]


def test_target_game_outcome_cannot_change_its_features():
    leaky = LOGS.copy()
    mask = (leaky["player_id"] == P1) & (leaky["game_id"] == 6)
    leaky.loc[mask, ["minutes", "started"]] = [48.0, 1.0]
    a, b = _features().loc[(P1, 6), ALL_FEATURES], _features(logs=leaky).loc[(P1, 6), ALL_FEATURES]
    pd.testing.assert_series_equal(a, b)


def test_future_games_cannot_change_features():
    future = LOGS.copy()
    future.loc[future["game_id"] >= 7, "minutes"] = 1.0
    a = _features().loc[(P1, 6), ALL_FEATURES]
    b = _features(logs=future).loc[(P1, 6), ALL_FEATURES]
    pd.testing.assert_series_equal(a, b)


def test_dnp_rows_are_history_but_not_minutes():
    f = _features().loc[(P1, 4)]
    # Before game 4: played 30, 20, then a DNP.
    assert f["min_last1"] == 20 and f["min_mean3"] == pytest.approx(25.0)
    assert f["dnp_last_listed"] == 1.0
    assert f["dnp_rate10"] == pytest.approx(1 / 3)
    assert f["played_streak"] == 0.0
    g = _features().loc[(P1, 6)]
    assert g["dnp_last_listed"] == 0.0 and g["played_streak"] == 2.0


def test_starter_history_uses_prior_games_only():
    f = _features().loc[(P1, 6)]
    assert f["started_last"] == 1.0  # game 5 start, NOT game 6's own (bench) flag
    assert f["start_rate5"] == pytest.approx(3 / 4)
    h = _features().loc[(P1, 7)]
    assert h["started_last"] == 0.0 and h["games_since_start"] == 1.0


def test_trade_is_detected_and_team_history_restarts():
    f = _features()
    first = f.loc[(P1, 7)]
    assert first["traded"] == 1.0
    assert first["games_with_team"] == 0.0 and np.isnan(first["team_min_mean5"])
    assert first["min_last1"] == 10  # career history still carries over
    second = f.loc[(P1, 8)]
    assert second["traded"] == 0.0 and second["games_with_team"] == 1.0 and second["team_min_mean5"] == 25.0


def test_schedule_rest_and_back_to_back_come_from_the_schedule():
    sched = schedule_context(GAMES).set_index(["game_id", "team_id"])
    assert sched.loc[(3, TEAM_A), "rest_days"] == 1.0 and sched.loc[(3, TEAM_A), "back_to_back"] == 1.0
    assert sched.loc[(4, TEAM_A), "rest_days"] == 3.0 and sched.loc[(4, TEAM_A), "back_to_back"] == 0.0
    assert sched.loc[(1, TEAM_A), "rest_days"] == 7.0  # season opener: fully rested
    # Within the previous 7 days of game 4 (Nov 7): games on Nov 1, 3, 4.
    assert sched.loc[(4, TEAM_A), "games_prev_7d"] == 3.0
    # Team C's first game: P1's new team had no earlier game.
    assert sched.loc[(7, TEAM_C), "rest_days"] == 7.0


def test_rest_counts_a_game_with_no_box_score():
    # P2's box score for game 3 is missing; the team still played that night.
    logs = LOGS[~((LOGS["player_id"] == P2) & (LOGS["game_id"] == 3))]
    f = _features(logs=logs).loc[(P2, 4)]
    assert f["rest_days"] == 3.0  # from game 3 (Nov 4), not game 2 (Nov 3)
    assert f["team_games_missed"] == 1.0  # the schedule shows a team game he has no box score for
    assert f["min_last1"] == 14


def test_returning_player_counts_missed_team_games():
    logs = LOGS[~((LOGS["player_id"] == P1) & LOGS["game_id"].isin([4, 5]))]
    f = _features(logs=logs).loc[(P1, 6)]
    assert f["team_games_missed"] == 3.0  # games 3 (DNP), 4, 5
    assert f["days_since_played"] == (pd.Timestamp("2023-11-12") - pd.Timestamp("2023-11-03")).days


def test_new_season_resets_season_to_date_and_carries_previous_season():
    games = pd.concat([GAMES, _games([(20, "2024-10-25", TEAM_C, TEAM_B, "final", 2024)])], ignore_index=True)
    logs = pd.concat([LOGS, _logs([(P1, 20, TEAM_C, 33, 1)])], ignore_index=True)
    f = _features(games=games, logs=logs).loc[(P1, 20)]
    assert np.isnan(f["season_mean"]) and f["season_games"] == 0.0
    assert f["prev_season_mean"] == pytest.approx(np.mean([30, 20, 34, 36, 10, 25, 28, 31]))
    assert f["season_progress"] == 0.0 and f["rest_days"] == 7.0


def test_game_inside_the_result_lag_is_not_yet_history():
    # Prediction made 2 hours after game 5 tipped off: game 5 may still be in progress.
    cutoff = GAMES.loc[GAMES["game_id"] == 5, "scheduled_start"].iloc[0] + pd.Timedelta(hours=2)
    rows = pd.DataFrame({"player_id": [P1], "game_id": [6], "team_id": [TEAM_A]})
    f = _features(rows=rows, cutoff=cutoff).iloc[0]
    assert f["min_last1"] == 34  # game 4, not game 5
    later = _features(rows=rows, cutoff=cutoff + pd.Timedelta(hours=3)).iloc[0]
    assert later["min_last1"] == 36


def test_unfinished_game_is_not_history():
    games = GAMES.copy()
    games.loc[games["game_id"] == 5, "status"] = "in_progress"
    f = _features(games=games).loc[(P1, 6)]
    assert f["min_last1"] == 34


def test_truncated_world_gives_identical_features():
    rows = LOGS[["player_id", "game_id", "team_id"]].reset_index(drop=True)
    assert lookahead_violations(GAMES, LOGS, rows) == []


def test_truncation_hides_results_but_keeps_the_schedule():
    t_cut = GAMES.loc[GAMES["game_id"] == 4, "scheduled_start"].iloc[0]
    g, lg = truncate_to_cutoff(GAMES, LOGS, t_cut)
    assert set(g["game_id"]) == set(GAMES["game_id"])
    assert (g.loc[g["game_id"] > 4, "status"] == "scheduled").all() and g.loc[g["game_id"] > 4, "home_score"].isna().all()
    assert lg["game_id"].max() == 4


def test_team_strength_uses_only_final_games_before_cutoff():
    f = _features()
    assert f.loc[(P2, 1), "team_net"] == 0.0  # nothing played yet
    # Before game 3, team A has played 2 games: won 100-95 at home, lost 100-95 away.
    assert f.loc[(P1, 3), "team_net"] == pytest.approx(0.0)
    assert f.loc[(P1, 4), "team_net"] == pytest.approx(5 / (3 + 5))


def test_historical_dataset_refuses_the_prospective_season():
    with pytest.raises(ValueError, match="prospective"):
        build_historical_dataset(GAMES, LOGS, last_season=PROSPECTIVE_SEASON)


def test_historical_dataset_targets_and_eligibility():
    data = build_historical_dataset(GAMES, LOGS, first_season=2023, last_season=2023).set_index(["player_id", "game_id"])
    dnp = data.loc[(P1, 3)]
    assert not dnp["played"] and np.isnan(dnp["minutes"]) and dnp["minutes_or_zero"] == 0.0
    assert data.loc[(P1, 4), "minutes"] == 34 and data.loc[(P1, 4), "played"]
    assert not data.loc[(NEW, 9), "eligible"]
