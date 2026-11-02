"""NBA Stat Model V1, on small synthetic frames (no database): the information
boundary, DNP handling, the test-fold seal, and the distributions' arithmetic."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from app.nba.minutes.dataset import build_historical_dataset
from app.nba.stats import models as sm
from app.nba.stats.experiment import near_lines, seal_test_outcomes, stat_v1_predictions
from app.nba.stats.features import STATS, add_stat_history

TEAM_A, TEAM_B, TEAM_C = 1, 2, 3
P1, P2 = 10, 11


def _games(n=8, season=2023):
    rows = []
    for i in range(n):
        date = pd.Timestamp("2023-11-01") + pd.Timedelta(days=2 * i)
        rows.append(
            {
                "game_id": i + 1, "season_start_year": season, "season_type": "regular", "game_date": date,
                "scheduled_start": date + pd.Timedelta(hours=24), "status": "final", "home_team_id": TEAM_A, "away_team_id": TEAM_B,
                "home_score": 100.0, "away_score": 95.0, "box_score_state": "ingested",
            }
        )
    return pd.DataFrame(rows)


def _logs(minutes=(30, 20, "DNP", 40, 30, 10, 36, 25), points=(15, 8, 0, 24, 18, 2, 20, 14), team=TEAM_A):
    rows = []
    for i, (m, p) in enumerate(zip(minutes, points)):
        dnp = m == "DNP"
        rows.append(
            {
                "player_id": P1, "game_id": i + 1, "team_id": team, "did_not_play": dnp, "started": np.nan if dnp else 1.0, "minutes": np.nan if dnp else float(m),
                "points": np.nan if dnp else float(p), "rebounds": np.nan if dnp else float(p) / 2, "assists": np.nan if dnp else float(p) / 3,
            }
        )
    return pd.DataFrame(rows)


def _frame(games=None, logs=None):
    games = _games() if games is None else games
    logs = _logs() if logs is None else logs
    data = build_historical_dataset(games, logs[["player_id", "game_id", "team_id", "did_not_play", "started", "minutes"]])
    out = add_stat_history(data, logs[["player_id", "game_id", "did_not_play", "minutes", *STATS]], games)
    return out.set_index("game_id")


def test_history_uses_only_earlier_games():
    f = _frame()
    # Before game 5: played games 1, 2 and 4 (15, 8, 24); game 3 was a DNP.
    assert f.loc[5, "points_last_n"] == pytest.approx((15 + 8 + 24) / 3)
    assert f.loc[5, "points_season"] == pytest.approx((15 + 8 + 24) / 3)


def test_first_game_has_no_history():
    f = _frame()
    assert np.isnan(f.loc[1, "points_last_n"])
    assert not f.loc[1, "eligible"]


def test_changing_the_target_game_does_not_move_its_features():
    base = _frame()
    logs = _logs()
    logs.loc[logs["game_id"] == 5, ["points", "rebounds", "assists", "minutes"]] = [99.0, 99.0, 99.0, 48.0]
    changed = _frame(logs=logs)
    cols = [c for c in base.columns if any(c.startswith(f"{s}_") for s in STATS) or c.startswith(("min_ewm", "weight"))]
    pd.testing.assert_series_equal(base.loc[5, cols].astype(float), changed.loc[5, cols].astype(float))
    assert changed.loc[6, "points_last_n"] != base.loc[6, "points_last_n"]  # it does count from the next game on


def test_a_game_inside_the_result_lag_is_not_read():
    games = _games()
    # Game 5 now tips off 2 hours after game 4, inside the 4-hour lag.
    games.loc[games["game_id"] == 5, "scheduled_start"] = games.loc[games["game_id"] == 4, "scheduled_start"].iloc[0] + pd.Timedelta(hours=2)
    f = _frame(games=games)
    assert f.loc[5, "points_last_n"] == pytest.approx((15 + 8) / 2)


def test_a_did_not_play_is_not_a_zero():
    f = _frame()
    # DNP at game 3: game 4's history is 15 and 8, with no zero averaged in.
    assert f.loc[4, "points_last_n"] == pytest.approx(11.5)
    assert f.loc[3, "actual_points"] != f.loc[3, "actual_points"]  # no outcome for the DNP
    assert not f.loc[3, "played"]


def test_decayed_rate_matches_a_hand_calculation():
    f = _frame()
    h = 10
    d = 0.5 ** (1 / h)
    # Before game 3 (DNP) the history is game 1 then game 2, the later weighted 1.
    rate = (15 * d + 8 + 50 * 0.4) / (30 * d + 20 + 50)
    got = sm.shrunk_rate(f.loc[[3]], "points", {b: 0.4 for b in sm.MINUTES_BAND_LABELS}, h, 50.0)
    assert got.iloc[0] == pytest.approx(rate)


def test_shrinkage_pulls_thin_histories_towards_the_prior():
    f = _frame().loc[[2]]  # one earlier game: 15 points in 30 minutes
    prior = {b: 0.1 for b in sm.MINUTES_BAND_LABELS}
    assert sm.shrunk_rate(f, "points", prior, 25, 200.0).iloc[0] < sm.shrunk_rate(f, "points", prior, 25, 20.0).iloc[0] < 0.5


def test_a_traded_player_keeps_his_rate():
    logs = _logs()
    logs.loc[logs["game_id"] >= 5, "team_id"] = TEAM_C
    games = _games()
    games.loc[games["game_id"] >= 5, "away_team_id"] = TEAM_C
    f = _frame(games=games, logs=logs)
    assert f.loc[5, "traded"] == 1.0
    assert f.loc[5, "points_last_n"] == pytest.approx((15 + 8 + 24) / 3)


def test_season_average_restarts_but_rate_carries_over():
    games = _games()
    games.loc[games["game_id"] >= 5, "season_start_year"] = 2024
    f = _frame(games=games)
    assert np.isnan(f.loc[5, "points_season"])
    assert not np.isnan(f.loc[5, "points_ewm25"])


def test_selection_stage_removes_every_test_fold_row():
    frame = pd.DataFrame({"season_start_year": [2022, 2023, 2024, 2025], "x": [1, 2, 3, 4]})
    assert seal_test_outcomes(frame, "selection")["season_start_year"].max() == 2023
    assert seal_test_outcomes(frame, "test")["season_start_year"].max() == 2025


def test_tuning_works_when_the_validation_season_has_no_v1_minutes():
    rng = np.random.default_rng(0)
    n = 300
    rows = pd.DataFrame({"season_start_year": np.repeat([2018, 2019, 2020], n), "minutes": 30.0, "min_ewm4": 30.0, "v1_minutes": [np.nan] * (2 * n) + [30.0] * n})
    rows["actual_points"] = rng.poisson(15.0, 3 * n).astype(float)
    for h in sm.HALFLIFE_GRID:
        rows[f"points_ewm{h}"] = 15.0 + rng.normal(0, 3, 3 * n)
        rows[f"min_ewm{h}"] = 30.0
        rows[f"weight{h}"] = 5.0
    mean, _, meta = stat_v1_predictions(rows, "points", [2020], log=lambda *_: None)
    assert all(np.isfinite(g["val_mae"]) for g in meta[2020]["grid"])
    assert mean[rows["season_start_year"] == 2020].notna().all()
    assert mean[rows["season_start_year"] < 2020].isna().all()  # only the fold itself is predicted


def test_prior_rates_only_read_the_rows_given():
    train = pd.DataFrame({"min_ewm4": [10.0, 30.0], "minutes": [10.0, 30.0], "actual_points": [2.0, 15.0]})
    prior = sm.fit_prior_rates(train, "points")
    assert prior["<15"] == pytest.approx(0.2)
    assert prior["25-32"] == pytest.approx(0.5)
    assert prior["32+"] == pytest.approx(17.0 / 40.0)  # empty band: the overall rate


def test_negative_binomial_recovers_its_alpha():
    rng = np.random.default_rng(3)
    mu, alpha = 20.0, 0.2
    n = 1 / alpha
    y = rng.negative_binomial(n, n / (n + mu), size=200_000)
    assert sm.fit_alpha_single(y, np.full(len(y), mu)) == pytest.approx(alpha, abs=0.01)


def test_buckets_borrow_the_pooled_alpha_when_thin():
    mu = np.full(500, 25.0)
    y = np.random.default_rng(1).poisson(mu) * 1.3
    alphas = sm.fit_alpha_buckets(y, mu)
    pooled = sm.fit_alpha_single(y, mu)
    assert alphas[0] == pytest.approx(pooled)  # the empty low bucket


def test_crps_matches_a_direct_sum_and_prefers_the_right_mean():
    y = np.array([20.0])
    grid = np.arange(sm.PMF_GRID_MAX)
    direct = ((sps.poisson.cdf(grid, 20.0) - (20.0 <= grid)) ** 2).sum()
    assert sm.crps(y, np.array([20.0]))[0] == pytest.approx(direct)
    assert sm.crps(y, np.array([20.0]))[0] < sm.crps(y, np.array([30.0]))[0]


def test_randomised_pit_is_uniform_when_the_distribution_is_right():
    rng = np.random.default_rng(5)
    mu = np.full(100_000, 3.0)
    n = 1 / 0.2
    y = rng.negative_binomial(n, n / (n + mu))
    pit = sm.randomised_pit(y, mu, np.full(len(y), 0.2))
    assert ((pit >= 0.1) & (pit <= 0.9)).mean() == pytest.approx(0.8, abs=0.01)


def test_over_a_half_line_needs_the_next_whole_number():
    # P(X >= 22) is P(over 21.5).
    assert sm.prob_at_least(22, np.array([20.0]))[0] == pytest.approx(1 - sps.poisson.cdf(21, 20.0))


def test_near_lines_straddle_the_mean():
    lines = near_lines(np.array([22.4]))
    assert lines["below"][0] == 21.5 and lines["above"][0] == 22.5
