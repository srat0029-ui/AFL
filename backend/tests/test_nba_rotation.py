"""NBA rotation layer (V1.5): participation labels and features, leakage,
team-minutes reconciliation, overtime, chronological classifiers. Synthetic
frames, no database."""

import numpy as np
import pandas as pd
import pytest

from app.nba.minutes.features import build_features, truncate_to_cutoff
from app.nba.rotation import evaluation as rev
from app.nba.rotation.allocation import reconcile_frame, reconcile_team, team_totals
from app.nba.rotation.dataset import build_rotation_dataset
from app.nba.rotation.features import EXTRA_FEATURES, PARTICIPATION_FEATURES, add_participation_features
from app.nba.rotation.metrics import confusion_at, expected_calibration_error, prob_metrics
from app.nba.rotation.models import CLASSIFIERS, baseline_last_listing, fit_predict_proba
from app.nba.rotation.overtime import OvertimeModel, overtime_periods
from app.nba.rotation.universe import attach_outcomes, reconstructed_roster
from tests.test_nba_minutes_features import GAMES, LOGS, NEW, P1, P2, TEAM_A, TEAM_B, TEAM_C, _logs


def _participation(games=GAMES, logs=LOGS, rows=None, cutoff=None):
    rows = rows if rows is not None else logs[["player_id", "game_id", "team_id"]]
    base = build_features(games, logs, rows.reset_index(drop=True), cutoff=cutoff)
    return add_participation_features(base, games, logs).set_index(["player_id", "game_id"])


# --- labels ----------------------------------------------------------------


def test_participation_and_rotation_labels():
    logs = pd.concat([LOGS, _logs([(P2, 5, TEAM_B, 7, 0)])], ignore_index=True)
    logs.loc[len(logs)] = {"player_id": P2, "game_id": 6, "team_id": TEAM_B, "did_not_play": False, "started": 0.0, "minutes": np.nan}
    d = build_rotation_dataset(GAMES, logs, universe="listed", first_season=2023, last_season=2023).set_index(["player_id", "game_id"])
    assert d.loc[(P1, 3), "y_play"] == 0.0 and d.loc[(P1, 3), "y_rot10"] == 0.0 and d.loc[(P1, 3), "y_rot5"] == 0.0  # DNP
    assert d.loc[(P2, 5), "y_play"] == 1.0 and d.loc[(P2, 5), "y_rot5"] == 1.0 and d.loc[(P2, 5), "y_rot10"] == 0.0  # 7-minute cameo
    assert d.loc[(P1, 4), "y_rot10"] == 1.0
    # played but minutes not recorded: played, rotation label unknown (not guessed)
    assert d.loc[(P2, 6), "y_play"] == 1.0 and np.isnan(d.loc[(P2, 6), "y_rot10"])
    # a player with no history is KEPT in the participation universe
    assert d.loc[(NEW, 9), "no_history"] == 1.0


# --- features / DNP / trades / absences -----------------------------------


def test_listing_features_count_dnp_as_zero_minutes():
    f = _participation().loc[(P1, 4)]
    # listings before game 4: 30, 20, DNP
    assert f["play_rate5"] == pytest.approx(2 / 3)
    assert f["min0_mean5"] == pytest.approx(50 / 3)
    assert f["rot_rate10"] == pytest.approx(2 / 3)
    assert f["listings_known"] == 3.0 and f["no_history"] == 0.0


def test_participation_features_survive_a_trade():
    f = _participation().loc[(P1, 7)]  # first game for team C
    assert f["traded"] == 1.0 and f["games_with_team"] == 0.0
    assert f["listings_known"] == 6.0  # his listings for team A still count


def test_long_absence_is_visible_before_the_game():
    logs = LOGS[~((LOGS["player_id"] == P1) & LOGS["game_id"].isin([4, 5]))]
    f = _participation(logs=logs).loc[(P1, 6)]
    assert f["team_games_missed"] == 3.0 and f["listings_known"] == 3.0


def test_new_player_has_no_history_but_gets_participation_features():
    f = _participation().loc[(NEW, 9)]
    assert f["no_history"] == 1.0 and f["listings_known"] == 0.0 and np.isnan(f["play_rate5"])


def test_rotation_rank_is_per_team_game_from_prior_games():
    f = _participation()
    # Game 1: P1 is the only team-A player listed (rank 1); game 2 likewise.
    assert f.loc[(P1, 3), "rank_mean5"] == 1.0
    assert f.loc[(P2, 3), "rank_mean5"] == 1.0  # only team-B player with minutes


def test_current_game_outcome_and_starter_flag_cannot_change_features():
    leaky = LOGS.copy()
    m = (leaky["player_id"] == P1) & (leaky["game_id"] == 6)
    leaky.loc[m, ["started", "minutes", "did_not_play"]] = [1.0, np.nan, True]
    a = _participation().loc[(P1, 6), PARTICIPATION_FEATURES]
    b = _participation(logs=leaky).loc[(P1, 6), PARTICIPATION_FEATURES]
    pd.testing.assert_series_equal(a, b)


def test_participation_features_from_truncated_world_are_identical():
    full = _participation()
    rows = LOGS[["player_id", "game_id", "team_id"]].reset_index(drop=True)
    for i, r in rows.iterrows():
        t_cut = full.loc[(r["player_id"], r["game_id"]), "t_cut"]
        g, lg = truncate_to_cutoff(GAMES, LOGS, t_cut)
        one = _participation(games=g, logs=lg, rows=rows.iloc[[i]]).iloc[0]
        for c in EXTRA_FEATURES:
            a, b = full.loc[(r["player_id"], r["game_id"]), c], one[c]
            assert (pd.isna(a) and pd.isna(b)) or a == pytest.approx(b), (i, c)


def test_prospective_cutoff_hides_a_game_inside_the_result_lag():
    cutoff = GAMES.loc[GAMES["game_id"] == 5, "scheduled_start"].iloc[0] + pd.Timedelta(hours=2)
    rows = pd.DataFrame({"player_id": [P1], "game_id": [6], "team_id": [TEAM_A]})
    f = _participation(rows=rows, cutoff=cutoff).iloc[0]
    assert f["listings_known"] == 4.0  # games 1-4; game 5 not yet known


# --- reconciliation --------------------------------------------------------


def test_alpha_zero_is_raw_and_alpha_one_hits_the_budget():
    m, p, s = np.array([34.0, 30.0, 25.0, 20.0, 15.0, 12.0, 10.0, 8.0]), np.array([1, 1, 1, 0.95, 0.9, 0.8, 0.6, 0.4]), np.array([5.1, 5.5, 6.1, 6.6, 7.1, 7.1, 7.1, 6.1])
    assert np.allclose(reconcile_team(m, p, s, 240.0, 0.0), m)
    adj = reconcile_team(m, p, s, 240.0, 1.0)
    assert (p * adj).sum() == pytest.approx(240.0)
    half = reconcile_team(m, p, s, 240.0, 0.5)
    assert (p * half).sum() == pytest.approx((p * m).sum() + 0.5 * (240.0 - (p * m).sum()))


def test_variance_weighting_protects_confident_high_minute_predictions():
    m, p, s = np.array([36.0, 14.0]), np.array([1.0, 1.0]), np.array([5.0, 7.0])
    adj = reconcile_team(m, p, s, 60.0, 1.0)
    assert (adj[1] - m[1]) > (adj[0] - m[0]) > 0  # the uncertain bench player absorbs more
    prop = reconcile_team(m, p, s, 60.0, 1.0, method="proportional")
    assert prop[0] - m[0] > adj[0] - m[0]  # proportional scaling moves the star more


def test_reconciliation_clips_to_playable_range():
    adj = reconcile_team(np.array([46.0, 2.0]), np.array([1.0, 1.0]), np.array([5.0, 6.0]), 120.0, 1.0)
    assert adj.max() <= 48.0 and adj.min() >= 0.0
    down = reconcile_team(np.array([30.0, 2.0]), np.array([1.0, 1.0]), np.array([5.0, 6.0]), 0.0, 1.0)
    assert down.min() >= 0.0


def test_frame_reconciliation_matches_per_team_and_keeps_teams_separate():
    f = pd.DataFrame({
        "game_id": [1, 1, 1, 1, 1], "team_id": [1, 1, 2, 2, 2], "m": [30.0, 20.0, 34.0, 25.0, 9.0],
        "p": [1.0, 0.9, 1.0, 0.95, 0.5], "s": [5.0, 6.6, 5.1, 6.1, 6.1], "b": [240.0] * 5,
    })
    adj = reconcile_frame(f, m_col="m", p_col="p", sigma_col="s", budget_col="b", alpha=0.75)
    np.testing.assert_allclose(adj[:2], reconcile_team(f.m[:2], f.p[:2], f.s[:2], 240.0, 0.75))
    np.testing.assert_allclose(adj[2:], reconcile_team(f.m[2:], f.p[2:], f.s[2:], 240.0, 0.75))


def test_team_totals_compare_expectation_with_actual():
    f = pd.DataFrame({"game_id": [1, 1], "team_id": [1, 1], "player_id": [1, 2], "m": [30.0, 10.0], "p": [1.0, 0.5], "minutes": [32.0, np.nan]})
    t = team_totals(f, "m", "p").iloc[0]
    assert t["expected"] == 35.0 and t["actual"] == 32.0


# --- overtime -------------------------------------------------------------


def test_overtime_periods_from_box_score_minutes():
    rows = pd.DataFrame({"game_id": [1] * 2 + [2] * 2, "team_id": [1, 2, 1, 2], "minutes": [241.0, 240.0, 290.0, 289.0]})
    ot = overtime_periods(rows.assign(player_id=0))
    assert ot.loc[1] == 0 and ot.loc[2] == 2


def test_overtime_budget_is_fitted_on_training_games_only():
    train = pd.DataFrame({"ot_periods": [0] * 9 + [1], "abs_net_diff": np.linspace(0, 10, 10)})
    test = pd.DataFrame({"ot_periods": [3, 3], "abs_net_diff": [1.0, 2.0]})
    m = OvertimeModel(False).fit(train)
    assert m.rate_ == pytest.approx(0.1) and m.budget(test)[0] == pytest.approx(240 + 25 * 0.1)


# --- classifiers -----------------------------------------------------------


def _synthetic_listings(seasons=range(2018, 2024), n=500, seed=0):
    rng = np.random.default_rng(seed)
    frames = []
    for s in seasons:
        f = pd.DataFrame({c: rng.normal(0, 1, n) for c in PARTICIPATION_FEATURES})
        f["dnp_last_listed"] = rng.integers(0, 2, n).astype(float)
        f["min_last1"] = rng.uniform(0, 40, n)
        f["rot_rate10"] = rng.uniform(0, 1, n)
        f["dnp_rate10"] = rng.uniform(0, 1, n)
        logit = 2.5 - 3 * f["dnp_last_listed"] + 0.5 * f["min0_mean10"]
        f["y_play"] = (rng.uniform(0, 1, n) < 1 / (1 + np.exp(-logit))).astype(float)
        f["season_start_year"] = s
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


def test_walk_forward_classifier_trains_only_on_earlier_seasons(monkeypatch):
    seen = []
    real = rev.fit_predict_proba

    def spy(cand, params, train, test, features, target):
        seen.append((train["season_start_year"].max(), test["season_start_year"].min()))
        return real(cand, params, train, test, features, target)

    monkeypatch.setattr(rev, "fit_predict_proba", spy)
    preds, _ = rev.walk_forward_clf(_synthetic_listings(), "y_play", ["logistic"], folds=(2022, 2023), log=lambda *_: None)
    assert all(tr < te for tr, te in seen)
    assert set(preds["season_start_year"]) == {2022, 2023}


def test_classifier_preprocessing_is_fitted_on_training_rows_only():
    d = _synthetic_listings()
    train, test = d[d["season_start_year"] < 2023], d[d["season_start_year"] == 2023].copy()
    model, _ = fit_predict_proba(CLASSIFIERS["logistic"], {"C": 1.0}, train, test, PARTICIPATION_FEATURES, "y_play")
    imputer = model.estimator.named_steps["simpleimputer"]
    np.testing.assert_allclose(imputer.statistics_, train[PARTICIPATION_FEATURES].median().to_numpy())


def test_learned_model_beats_base_rate_on_learnable_data():
    preds, _ = rev.walk_forward_clf(_synthetic_listings(n=1500), "y_play", ["logistic"], folds=(2023,), log=lambda *_: None)
    s = rev.summarize_clf(preds, ["base_rate", "logistic"], "y_play", [2023])
    assert s["logistic"]["log_loss"] < s["base_rate"]["log_loss"]


def test_last_listing_baseline_uses_training_rates():
    train = pd.DataFrame({"dnp_last_listed": [1.0, 1.0, 0.0, 0.0], "min_last1": [np.nan, np.nan, 30.0, 5.0], "y_play": [0.0, 1.0, 1.0, 1.0]})
    test = pd.DataFrame({"dnp_last_listed": [1.0, np.nan], "min_last1": [np.nan, np.nan]})
    p = baseline_last_listing(train, test, "y_play")
    assert p[0] == pytest.approx(0.5) and p[1] == pytest.approx(0.75)  # no previous listing -> training prior


def test_probability_metrics_and_confusion():
    y = np.array([1, 1, 0, 0])
    p = np.array([0.9, 0.6, 0.4, 0.1])
    m = prob_metrics(p, y)
    assert m["auc"] == 1.0 and m["brier"] == pytest.approx(np.mean((p - y) ** 2))
    c = confusion_at(p, y, thresholds=(0.5,))["0.5"]
    assert (c["tp"], c["fp"], c["fn"], c["tn"]) == (2, 0, 0, 2)
    assert expected_calibration_error(np.full(1000, 0.7), (np.arange(1000) < 700).astype(float)) == pytest.approx(0.0, abs=1e-9)



# --- reconstructed roster universe ---------------------------------------


def _roster_set(games=GAMES, logs=LOGS):
    r = reconstructed_roster(games, logs, first_season=2023, last_season=2023)
    return set(zip(r["player_id"], r["game_id"], r["team_id"]))


def test_trade_is_not_foreseen_until_the_new_team_lists_him():
    r = _roster_set()
    assert (P1, 7, TEAM_C) not in r  # first game for team C: no box score had shown the move yet
    assert (P1, 8, TEAM_C) in r and (P1, 9, TEAM_C) in r
    assert not any(p == P1 and t == TEAM_A and g >= 7 for p, g, t in r)  # gone from team A once seen elsewhere


def test_unlisted_player_stays_a_candidate_and_does_not_play():
    r = _roster_set()
    # P2's last listing is game 4 (team B); team B keeps playing games 5-9.
    for g in (5, 6, 7, 8, 9):
        assert (P2, g, TEAM_B) in r
    out = attach_outcomes(reconstructed_roster(GAMES, LOGS, first_season=2023, last_season=2023), LOGS).set_index(["player_id", "game_id"])
    assert out.loc[(P2, 6), "y_play"] == 0.0 and not out.loc[(P2, 6), "listed"]
    assert out.loc[(P1, 4), "y_play"] == 1.0 and out.loc[(P1, 3), "y_play"] == 0.0 and out.loc[(P1, 3), "listed"]


def test_new_player_is_never_a_foreseen_candidate():
    assert not any(p == NEW for p, _, _ in _roster_set())


def test_roster_membership_ignores_listings_after_the_cutoff():
    later = pd.concat([LOGS, _logs([(P2, 8, TEAM_B, 20, 1)])], ignore_index=True)
    before, after = _roster_set(), _roster_set(logs=later)
    assert {x for x in before if x[1] <= 8} == {x for x in after if x[1] <= 8}


def test_roster_universe_dataset_counts_unlisted_games():
    d = build_rotation_dataset(GAMES, LOGS, universe="roster", first_season=2023, last_season=2023).set_index(["player_id", "game_id"])
    assert d.loc[(P2, 5), "team_games_unlisted"] == 0.0  # listed in team B's previous game (4)
    assert d.loc[(P2, 7), "team_games_unlisted"] == 2.0  # team B games 5 and 6 without a listing
    assert d.loc[(P2, 7), "y_play"] == 0.0 and np.isnan(d.loc[(P2, 7), "minutes"])
