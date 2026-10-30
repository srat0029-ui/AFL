"""Minutes V2 building blocks: probabilistic teammate features and the
evidence recalibration of participation probabilities."""

import numpy as np
import pandas as pd
import pytest

from app.nba.minutes_v2 import upper_bound
from app.nba.minutes_v2.features import TEAMMATE_FEATURES, expected_teammate_features, oracle_teammate_features
from app.nba.minutes_v2.participation import STATES, EvidenceRecalibration, evidence_state, out_rule


def _random_roster(seed=3, team_games=40):
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(team_games):
        for j in range(int(rng.integers(12, 22))):
            rows.append({
                "game_id": g // 2, "team_id": g % 2, "listed": bool(rng.random() < 0.65),
                "min_ewm": np.nan if rng.random() < 0.1 else float(rng.uniform(0, 38)),
                "start_rate5": np.nan if rng.random() < 0.1 else float(rng.choice([0, 0.2, 0.6, 1.0])),
                "team_games_unlisted": float(rng.choice([0, 0, 0, 1, 2, 4, 5, 20])),
            })
    return pd.DataFrame(rows)


def test_binary_q_reproduces_step_1_oracle_features_exactly():
    frame = _random_roster()
    assert upper_bound.TEAMMATE_FEATURES == TEAMMATE_FEATURES
    pd.testing.assert_frame_equal(oracle_teammate_features(frame), upper_bound.teammate_absence_features(frame), check_dtype=False)


def test_expected_features_are_linear_in_each_teammates_probability():
    frame = _random_roster(team_games=2)
    rng = np.random.default_rng(1)
    q = rng.random(len(frame))
    low, high = q.copy(), q.copy()
    j = 4  # vary one teammate
    low[j], high[j] = 0.0, 1.0
    mid = q.copy()
    mid[j] = 0.3
    f_low, f_high, f_mid = (expected_teammate_features(frame, x) for x in (low, high, mid))
    others = (frame["game_id"] == frame.loc[j, "game_id"]) & (frame["team_id"] == frame.loc[j, "team_id"]) & (frame.index != j)
    pd.testing.assert_frame_equal(f_mid[others], 0.7 * f_low[others] + 0.3 * f_high[others])
    pd.testing.assert_series_equal(f_mid.loc[j], f_low.loc[j], check_names=False)  # his own q never enters his features


def test_expected_features_reject_missing_probabilities():
    frame = _random_roster(team_games=2)
    with pytest.raises(ValueError):
        expected_teammate_features(frame, np.full(len(frame), np.nan))


@pytest.mark.parametrize(
    ("evidence", "state"),
    [
        ({"state": "listed", "status": "out", "source_status": "Out"}, "out"),
        ({"state": "listed", "status": None, "source_status": "Day-To-Day"}, "day_to_day"),
        ({"state": "listed", "status": "questionable", "source_status": "Questionable"}, "listed_other"),
        ({"state": "no_longer_listed"}, "no_longer_listed"),
        ({"state": "never_listed"}, "never_listed"),
        ({"state": "feed_not_read_by_cutoff"}, "feed_not_read"),
        (None, "feed_not_read"),
    ],
)
def test_evidence_state(evidence, state):
    assert evidence_state(evidence) == state


def test_out_rule_only_touches_out():
    assert out_rule([0.3, 0.9, 0.5], ["out", "day_to_day", "never_listed"]).tolist() == [0.0, 0.9, 0.5]


def _synthetic(n=6000, seed=0):
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.05, 0.95, n)
    states = rng.choice(["never_listed", "out", "day_to_day", "no_longer_listed"], n, p=[0.7, 0.1, 0.1, 0.1])
    z = np.log(p / (1 - p)) + np.select([states == "out", states == "day_to_day"], [-5.0, -1.0], 0.0)
    y = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(float)
    return p, states, y


def test_recalibration_learns_what_each_state_says():
    p, states, y = _synthetic()
    model = EvidenceRecalibration(penalty=1.0).fit(p, states, y)
    adj = model.predict(p, states)
    for s in ("out", "day_to_day", "never_listed"):
        m = states == s
        assert abs(adj[m].mean() - y[m].mean()) < 0.03, s  # calibrated within each state
    assert model.coef_["out"][0] < -3 and -2 < model.coef_["day_to_day"][0] < 0
    assert abs(model.coef_["never_listed"][0]) < 0.2  # V1.5 was already right there


def test_recalibration_shrinks_to_no_change_without_data():
    p, states, y = _synthetic(n=500)
    model = EvidenceRecalibration(penalty=1.0).fit(p, states, y)
    assert model.coef_["feed_not_read"] == (0.0, 0.0) and model.coef_["listed_other"] == (0.0, 0.0)
    probe = np.array([0.2, 0.7])
    assert np.allclose(model.predict(probe, ["feed_not_read", "listed_other"]), probe)
    strong = EvidenceRecalibration(penalty=1e6).fit(p, states, y)
    assert np.allclose(strong.predict(p, states), p, atol=1e-3)


def test_recalibration_rejects_unknown_states():
    with pytest.raises(ValueError):
        EvidenceRecalibration().fit([0.5], ["questionable"], [1])
    assert len(STATES) == len(set(STATES))
