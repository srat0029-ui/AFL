"""Tests for live_models.py (promoted-model loading/dispatch) and
live_confidence.py (scoring archetype, lineup-adjusted confidence tiers,
rare-event warnings) - Section 22 of the live-projection brief.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.player_modelling.disposal_features import DisposalFeatureRow
from app.player_modelling.goal_features import GoalFeatureRow
from app.player_modelling.live_confidence import (
    ARCHETYPE_BUCKETS,
    classify_scoring_archetype,
    live_disposal_confidence,
    live_goal_confidence,
    rare_event_warning,
)
from app.player_modelling.live_models import fit_live_disposal_model, fit_live_goal_model


@dataclass
class _FakeRun:
    model_name: str
    feature_names: list
    config_json: dict


BASE = datetime(2020, 1, 1, tzinfo=timezone.utc)
_FEATURES = ("disposals_last5_avg", "disposals_career_avg")


def _disposal_rows(n=60):
    rng = random.Random(7)
    rows = []
    for i in range(n):
        avg = rng.uniform(8, 25)
        rows.append(
            DisposalFeatureRow(
                player_id=i % 6, match_id=i, team_id=10, opponent_team_id=20, season_year=2020, round_number=1,
                is_final=False, scheduled_start=BASE + timedelta(days=i), disposals=int(avg + rng.uniform(-2, 2)),
                games_of_history=i, features={"disposals_last5_avg": avg, "disposals_career_avg": avg - 1},
            )
        )
    return rows


_GOAL_FEATURES = ("goals_last5_avg", "goals_career_avg")


def _goal_rows(n_players=40, n_games=16):
    """A larger, randomised fixture - statsmodels' NB maximum-likelihood fit
    (used by the hurdle model) needs enough sample variation to converge to
    a non-singular Hessian; a small fixture with few distinct feature
    values is too degenerate (see tests/test_goal_backtest.py's identical
    fix for the research-stage hurdle fit)."""
    rng = random.Random(11)
    rows = []
    match_id = 0
    for p in range(n_players):
        for g in range(n_games):
            rate = rng.uniform(0.0, 1.2)
            rows.append(
                GoalFeatureRow(
                    player_id=p, match_id=match_id, team_id=10, opponent_team_id=20, season_year=2020, round_number=1,
                    is_final=False, scheduled_start=BASE + timedelta(days=match_id),
                    goals=rng.choices([0, 1, 2, 3], weights=[6, 3, 1, 1])[0],
                    games_of_history=g, features={"goals_last5_avg": rate, "goals_career_avg": rate * rng.uniform(0.8, 1.2)},
                )
            )
            match_id += 1
    return rows


# --- live_models.py ---


def test_fit_live_disposal_model_baseline_family_needs_no_fitting():
    run = _FakeRun(model_name="disposals_baseline_last5", feature_names=[], config_json={})
    model = fit_live_disposal_model(_disposal_rows(), run)
    preds = model.predict_many(_disposal_rows(5))
    assert len(preds) == 5
    assert model.nb_alpha > 0


def test_fit_live_disposal_model_ridge_family():
    run = _FakeRun(model_name="disposals_ridge", feature_names=list(_FEATURES), config_json={"alpha": 5.0})
    model = fit_live_disposal_model(_disposal_rows(), run)
    preds = model.predict_many(_disposal_rows(3))
    assert all(p >= 0 for p in preds)


def test_fit_live_disposal_model_rejects_unknown_family():
    run = _FakeRun(model_name="disposals_totally_made_up", feature_names=list(_FEATURES), config_json={})
    with pytest.raises(ValueError):
        fit_live_disposal_model(_disposal_rows(), run)


def test_fit_live_goal_model_baseline_family():
    run = _FakeRun(model_name="goals_baseline_last5", feature_names=[], config_json={})
    model = fit_live_goal_model(_goal_rows(), run)
    assert model.distribution_kind == "nb"
    preds = model.predict_mean(_goal_rows(n_players=2, n_games=2))
    assert len(preds) == 4


def test_fit_live_goal_model_hurdle_family():
    run = _FakeRun(model_name="goals_hurdle", feature_names=list(_GOAL_FEATURES), config_json={})
    model = fit_live_goal_model(_goal_rows(), run)
    assert model.distribution_kind == "hurdle"
    assert model.predict_hurdle_params is not None
    p_score, mu_scored = model.predict_hurdle_params(_goal_rows(n_players=2, n_games=2))
    assert all(0.0 <= p <= 1.0 for p in p_score)
    assert all(mu > 0 for mu in mu_scored)


# --- live_confidence.py ---


def test_classify_scoring_archetype_boundaries():
    assert classify_scoring_archetype(0.0) == "very_low"
    assert classify_scoring_archetype(0.1) == "very_low"
    assert classify_scoring_archetype(0.15) == "occasional"
    assert classify_scoring_archetype(0.4) == "regular"
    assert classify_scoring_archetype(0.8) == "high_volume"
    assert classify_scoring_archetype(None) == "very_low"
    assert {b[0] for b in ARCHETYPE_BUCKETS} == {"very_low", "occasional", "regular", "high_volume"}


def test_live_disposal_confidence_downgrades_for_uncertain_lineup():
    stable = live_disposal_confidence(games_of_history=100, tog_last5_avg=85, disposals_last5_std=2.0, lineup_status="expected_in")
    uncertain = live_disposal_confidence(games_of_history=100, tog_last5_avg=85, disposals_last5_std=2.0, lineup_status="uncertain")
    assert stable.tier == "higher_confidence"
    assert uncertain.tier == "moderate_confidence"
    assert any("not confirmed" in w for w in uncertain.warnings)


def test_live_disposal_confidence_insufficient_history_stays_lowest_tier_even_if_uncertain():
    result = live_disposal_confidence(games_of_history=2, tog_last5_avg=85, disposals_last5_std=1.0, lineup_status="uncertain")
    assert result.tier == "insufficient_history"


def test_live_goal_confidence_reports_high_volume_warning():
    result = live_goal_confidence(games_of_history=50, tog_last5_avg=85, goals_last5_std=0.2, goals_career_avg=1.2, lineup_status="expected_in")
    assert result.scoring_archetype == "high_volume"
    assert any("under-predicted" in w for w in result.warnings)


def test_rare_event_warning_flags_high_disposal_thresholds():
    assert rare_event_warning("player_disposals", 35.0, 0.3) is not None
    assert rare_event_warning("player_disposals", 20.0, 0.5) is None


def test_rare_event_warning_flags_low_probability_regardless_of_threshold():
    assert rare_event_warning("player_disposals", 15.0, 0.05) is not None


def test_rare_event_warning_flags_goal_thresholds():
    assert rare_event_warning("player_goals", 3.0, 0.2) is not None
    assert rare_event_warning("player_goals", 1.0, 0.5) is None
