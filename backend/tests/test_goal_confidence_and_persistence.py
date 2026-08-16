"""Confidence-tier and persistence/API tests for the goal model — mirrors
test_disposal_metrics.py's confidence-tier tests and
test_disposal_persistence_api.py's persistence/API integration tests.
"""

import numpy as np
import pytest

from app.models import GoalModelRun, Player, PlayerGoalPrediction, Sport
from app.player_modelling.goal_backtest import GoalPredictionRecord
from app.player_modelling.goal_confidence import GoalConfidenceInputs, GoalConfidenceTier, classify_goal_confidence
from app.player_modelling.goal_evaluation import evaluate_goal_model
from app.player_modelling.goal_persistence import persist_goal_model_run
from app.player_modelling.market import PlayerMarket


def test_insufficient_history_below_min_games():
    tier = classify_goal_confidence(GoalConfidenceInputs(games_of_history=2, tog_last5_avg=80, goals_last5_std=0.3, league_low_tog_cutoff=60))
    assert tier == GoalConfidenceTier.INSUFFICIENT_HISTORY


def test_lower_confidence_for_unstable_tog():
    tier = classify_goal_confidence(GoalConfidenceInputs(games_of_history=50, tog_last5_avg=40, goals_last5_std=0.3, league_low_tog_cutoff=60))
    assert tier == GoalConfidenceTier.LOWER


def test_lower_confidence_for_high_scoring_variance():
    tier = classify_goal_confidence(GoalConfidenceInputs(games_of_history=50, tog_last5_avg=80, goals_last5_std=1.5, league_low_tog_cutoff=60))
    assert tier == GoalConfidenceTier.LOWER


def test_higher_confidence_for_established_stable_scorer():
    tier = classify_goal_confidence(GoalConfidenceInputs(games_of_history=100, tog_last5_avg=85, goals_last5_std=0.2, league_low_tog_cutoff=60))
    assert tier == GoalConfidenceTier.HIGHER


def test_moderate_confidence_for_mid_history():
    tier = classify_goal_confidence(GoalConfidenceInputs(games_of_history=15, tog_last5_avg=85, goals_last5_std=0.2, league_low_tog_cutoff=60))
    assert tier == GoalConfidenceTier.MODERATE


# --- Persistence / API ---


def _seed_player(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    player = Player(sport_id=sport.id, display_name="Test Forward", source="afltables", source_player_id="players/T/Test_Forward.html")
    db_session.add(player)
    db_session.commit()
    return player


def _predictions(player_id, team_id, n=20, kind="hurdle"):
    return [
        GoalPredictionRecord(
            player_id=player_id, match_id=i, team_id=team_id, season_year=2019 + (i % 2), is_final=False,
            games_of_history=i, tog_last5_avg=80.0, zero_goal_rate_last10=0.5, actual=i % 4,
            predicted_mean=0.5 + (i % 3) * 0.2, distribution_kind=kind,
            nb_alpha=1.5 if kind == "nb" else None,
            p_score=0.4 if kind == "hurdle" else None,
            mu_scored=1.5 if kind == "hurdle" else None,
            alpha_scored=1.0 if kind == "hurdle" else None,
        )
        for i in range(1, n + 1)
    ]


def test_persist_goal_model_run_creates_run_metrics_and_predictions(db_session):
    player = _seed_player(db_session)
    preds = _predictions(player.id, team_id=1)
    evaluation = evaluate_goal_model("hurdle", preds)

    run = persist_goal_model_run(
        db_session, model_name="goals_hurdle", feature_names=("goals_last5_avg",), config={},
        distribution_kind="hurdle", tune_start_year=2016, tune_end_year=2018, evaluation=evaluation,
        predictions=preds, is_promoted=True,
    )

    assert run.market == PlayerMarket.GOALS.value
    assert run.is_promoted is True
    stored = db_session.query(PlayerGoalPrediction).filter_by(model_run_id=run.id).all()
    assert len(stored) == len(preds)
    assert all(s.distribution_kind == "hurdle" for s in stored)


def test_persist_goal_model_run_upserts_wholesale_on_rerun(db_session):
    player = _seed_player(db_session)
    preds_v1 = _predictions(player.id, team_id=1, n=10)
    persist_goal_model_run(
        db_session, model_name="goals_hurdle", feature_names=(), config={}, distribution_kind="hurdle",
        tune_start_year=2016, tune_end_year=2018, evaluation=evaluate_goal_model("hurdle", preds_v1), predictions=preds_v1,
    )
    preds_v2 = _predictions(player.id, team_id=1, n=25)
    persist_goal_model_run(
        db_session, model_name="goals_hurdle", feature_names=(), config={}, distribution_kind="hurdle",
        tune_start_year=2016, tune_end_year=2018, evaluation=evaluate_goal_model("hurdle", preds_v2), predictions=preds_v2,
    )
    runs = db_session.query(GoalModelRun).filter_by(model_name="goals_hurdle").all()
    assert len(runs) == 1
    stored = db_session.query(PlayerGoalPrediction).filter_by(model_run_id=runs[0].id).all()
    assert len(stored) == 25


def test_goal_models_endpoint_returns_503_when_nothing_persisted(client):
    resp = client.get("/api/player-models/goals/backtest")
    assert resp.status_code == 503


def test_goal_backtest_summary_endpoint(db_session, client):
    player = _seed_player(db_session)
    preds = _predictions(player.id, team_id=1)
    persist_goal_model_run(
        db_session, model_name="goals_hurdle", feature_names=("goals_last5_avg",), config={}, distribution_kind="hurdle",
        tune_start_year=2016, tune_end_year=2018, evaluation=evaluate_goal_model("hurdle", preds), predictions=preds, is_promoted=True,
    )
    resp = client.get("/api/player-models/goals/backtest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_research_only"] is True
    assert data["promoted_model"]["model_name"] == "goals_hurdle"
    assert data["zero_goal"]["actual_p0"] is not None


def test_goal_player_history_endpoint_reconstructs_hurdle_probabilities(db_session, client):
    player = _seed_player(db_session)
    preds = _predictions(player.id, team_id=1)
    persist_goal_model_run(
        db_session, model_name="goals_hurdle", feature_names=("goals_last5_avg",), config={}, distribution_kind="hurdle",
        tune_start_year=2016, tune_end_year=2018, evaluation=evaluate_goal_model("hurdle", preds), predictions=preds, is_promoted=True,
    )
    resp = client.get(f"/api/player-models/goals/players/{player.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["predictions"]) == len(preds)
    first = data["predictions"][0]
    assert 0.0 <= first["prob_1_plus"] <= 1.0
    assert first["prob_1_plus"] >= first["prob_2_plus"] >= first["prob_3_plus"] >= first["prob_4_plus"]
