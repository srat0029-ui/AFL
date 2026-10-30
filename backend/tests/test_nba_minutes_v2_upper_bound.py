"""Minutes V2 step 1 (offline upper bound): the teammate-absence features and
the clustered paired bootstrap. The experiment itself runs on the full local
history (python -m app.nba.minutes_v2.upper_bound), not in the test suite."""

import numpy as np
import pandas as pd
import pytest

from app.nba.minutes_v2.upper_bound import TEAMMATE_FEATURES, paired_bootstrap, teammate_absence_features


def _team_game(game_id=1, team_id=10):
    # player: (dressed for game Y, EWMA minutes, start rate of last 5, team games since last listed)
    rows = {
        "star": (False, 34.0, 1.0, 0),  # newly absent starter
        "sixth": (True, 24.0, 0.2, 0),
        "returner": (True, 20.0, 0.8, 2),  # missed the last two team games, back now
        "long_out": (False, 18.0, 0.0, 12),  # absent for weeks: not "recent"
        "recent_out": (False, 10.0, 0.0, 3),  # absent since 3 team games: recent, not new
        "deep_bench": (True, np.nan, np.nan, 0),
    }
    return pd.DataFrame(
        [{"game_id": game_id, "team_id": team_id, "player": p, "listed": d, "min_ewm": e, "start_rate5": s, "team_games_unlisted": u} for p, (d, e, s, u) in rows.items()]
    )


def test_teammate_absence_features_exclude_self_and_follow_definitions():
    frame = _team_game()
    feats = teammate_absence_features(frame).set_index(frame["player"])
    assert list(feats.columns) == TEAMMATE_FEATURES
    sixth = feats.loc["sixth"]
    assert sixth["dressed_others"] == 2  # returner, deep_bench
    assert sixth["absent_new_min"] == 34.0 and sixth["absent_new_n"] == 1 and sixth["absent_new_starters"] == 1
    assert sixth["absent_recent_min"] == 34.0 + 10.0  # long_out (12 team games) is not recent
    assert sixth["returning_min"] == 20.0
    assert sixth["ewm_rank_dressed"] == 1  # most EWMA minutes among the dressed
    star = feats.loc["star"]  # his own absence is never counted as a teammate's
    assert star["absent_new_min"] == 0.0 and star["absent_new_n"] == 0 and star["dressed_others"] == 3
    returner = feats.loc["returner"]
    assert returner["returning_min"] == 0.0 and returner["ewm_rank_dressed"] == 2
    assert feats.loc["deep_bench", "ewm_rank_dressed"] == 3  # no history counts as 0 minutes


def test_teammate_features_never_mix_team_games():
    frame = pd.concat([_team_game(1, 10), _team_game(1, 20), _team_game(2, 10)], ignore_index=True)
    feats = teammate_absence_features(frame)
    single = teammate_absence_features(_team_game())
    for start in (0, 6, 12):
        block = feats.iloc[start:start + 6].reset_index(drop=True)
        pd.testing.assert_frame_equal(block, single)


def test_paired_bootstrap_sign_and_clustering():
    rng = np.random.default_rng(0)
    clusters = np.repeat(np.arange(200), 50)
    actual = rng.normal(25, 6, len(clusters))
    worse = actual + rng.normal(0, 5, len(clusters))
    better = actual + rng.normal(0, 4, len(clusters))
    res = paired_bootstrap(worse - actual, better - actual, clusters)
    assert res["mean_improvement"] > 0 and res["ci95"][0] > 0 and res["clusters"] == 200 and res["n"] == 10_000
    same = paired_bootstrap(worse - actual, worse - actual, clusters)
    assert same["mean_improvement"] == 0 and same["ci95"] == [0.0, 0.0]
    reverse = paired_bootstrap(better - actual, worse - actual, clusters)
    assert reverse["mean_improvement"] == pytest.approx(-res["mean_improvement"])
