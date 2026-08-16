"""Integration tests for disposal_persistence.py and the /api/player-models
endpoints - Section 22 (reproducible persisted predictions) and Section 23
(research API, never exposed as live betting advice).
"""

from datetime import datetime, timezone

import numpy as np

from app.player_modelling.disposal_backtest import PredictionRecord
from app.player_modelling.disposal_evaluation import evaluate_model
from app.player_modelling.disposal_persistence import persist_model_run
from app.player_modelling.market import PlayerMarket
from app.models import Player, PlayerDisposalPrediction, PlayerModelRun, Sport


def _seed_player(db_session, source_player_id="players/T/Test_Player.html"):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    player = Player(
        sport_id=sport.id,
        display_name="Test Player",
        source="afltables",
        source_player_id=source_player_id,
    )
    db_session.add(player)
    db_session.commit()
    return player


def _predictions(player_id, team_id, n=20):
    rng = np.random.default_rng(0)
    residuals = np.sort(rng.normal(0, 5, 100))
    return [
        PredictionRecord(
            player_id=player_id,
            match_id=i,
            team_id=team_id,
            season_year=2019 + (i % 2),
            is_final=False,
            games_of_history=i,
            tog_last5_avg=80.0,
            disposals_last5_std=3.0,
            actual=15 + (i % 10),
            predicted_mean=15.0 + (i % 5),
            nb_alpha=0.05,
            empirical_residuals=residuals,
        )
        for i in range(1, n + 1)
    ]


def test_persist_model_run_creates_run_metrics_and_predictions(db_session):
    player = _seed_player(db_session)
    preds = _predictions(player.id, team_id=1)
    evaluation = evaluate_model("ridge", preds, "nb")

    run = persist_model_run(
        db_session,
        model_name="disposals_ridge",
        feature_names=("disposals_last5_avg",),
        config={"alpha": 5.0},
        distribution_method="nb",
        tune_start_year=2016,
        tune_end_year=2018,
        evaluation=evaluation,
        predictions=preds,
        is_promoted=True,
    )

    assert run.market == PlayerMarket.DISPOSALS.value
    assert run.is_promoted is True
    stored_predictions = db_session.query(PlayerDisposalPrediction).filter_by(model_run_id=run.id).all()
    assert len(stored_predictions) == len(preds)
    assert {p.match_id for p in stored_predictions} == {p.match_id for p in preds}


def test_persist_model_run_upserts_wholesale_on_rerun(db_session):
    player = _seed_player(db_session)
    preds_v1 = _predictions(player.id, team_id=1, n=10)
    evaluation_v1 = evaluate_model("ridge", preds_v1, "nb")
    persist_model_run(
        db_session, model_name="disposals_ridge", feature_names=(), config={}, distribution_method="nb",
        tune_start_year=2016, tune_end_year=2018, evaluation=evaluation_v1, predictions=preds_v1,
    )

    preds_v2 = _predictions(player.id, team_id=1, n=25)  # a "rerun" with more eval rows
    evaluation_v2 = evaluate_model("ridge", preds_v2, "nb")
    persist_model_run(
        db_session, model_name="disposals_ridge", feature_names=(), config={}, distribution_method="nb",
        tune_start_year=2016, tune_end_year=2018, evaluation=evaluation_v2, predictions=preds_v2,
    )

    runs = db_session.query(PlayerModelRun).filter_by(model_name="disposals_ridge").all()
    assert len(runs) == 1  # upserted in place, not duplicated
    stored_predictions = db_session.query(PlayerDisposalPrediction).filter_by(model_run_id=runs[0].id).all()
    assert len(stored_predictions) == 25  # old predictions replaced wholesale, not appended


def test_player_models_endpoint_returns_503_when_nothing_persisted(client):
    resp = client.get("/api/player-models/disposals/backtest")
    assert resp.status_code == 503


def test_player_models_list_endpoint_marks_research_only(db_session, client):
    player = _seed_player(db_session)
    preds = _predictions(player.id, team_id=1)
    evaluation = evaluate_model("ridge", preds, "nb")
    persist_model_run(
        db_session, model_name="disposals_ridge", feature_names=("disposals_last5_avg",), config={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation=evaluation,
        predictions=preds, is_promoted=True,
    )

    resp = client.get("/api/player-models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_research_only"] is True
    assert len(data["runs"]) == 1
    assert data["runs"][0]["model_name"] == "disposals_ridge"
    assert data["runs"][0]["is_promoted"] is True


def test_disposal_backtest_summary_endpoint(db_session, client):
    player = _seed_player(db_session)
    preds = _predictions(player.id, team_id=1)
    evaluation = evaluate_model("ridge", preds, "nb")
    persist_model_run(
        db_session, model_name="disposals_ridge", feature_names=("disposals_last5_avg",), config={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation=evaluation,
        predictions=preds, is_promoted=True,
    )

    resp = client.get("/api/player-models/disposals/backtest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_research_only"] is True
    assert data["promoted_model"]["model_name"] == "disposals_ridge"
    assert data["within_2"] is not None


def test_disposal_player_history_endpoint_reconstructs_probabilities(db_session, client):
    player = _seed_player(db_session)
    preds = _predictions(player.id, team_id=1)
    evaluation = evaluate_model("ridge", preds, "nb")
    persist_model_run(
        db_session, model_name="disposals_ridge", feature_names=("disposals_last5_avg",), config={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation=evaluation,
        predictions=preds, is_promoted=True,
    )

    resp = client.get(f"/api/player-models/disposals/players/{player.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_research_only"] is True
    assert len(data["predictions"]) == len(preds)
    first = data["predictions"][0]
    assert 0.0 <= first["prob_20_plus"] <= 1.0
    assert first["interval_50"][0] <= first["interval_50"][1]


def test_disposal_player_history_endpoint_404s_for_unknown_player(db_session, client):
    resp = client.get("/api/player-models/disposals/players/99999")
    assert resp.status_code in (404, 503)  # 503 if no run persisted yet, 404 once one exists but player doesn't
