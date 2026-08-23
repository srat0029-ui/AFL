"""API-level tests for /api/v1/model-registry and its
/prospective-evaluation sub-endpoint: correct dataset labels (so a
consumer can never confuse historical backtest with prospective live
evaluation), and honest empty-state handling."""

from datetime import datetime, timezone


def test_model_registry_empty_db_returns_empty_lists_not_an_error(client, db_session):
    resp = client.get("/api/v1/model-registry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dataset_label"] == "Historical backtest"
    assert body["disposal_models"] == []
    assert body["promotion_events"] == []


def test_prospective_evaluation_empty_db_is_accumulating_state(client, db_session):
    resp = client.get("/api/v1/model-registry/prospective-evaluation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dataset_label"] == "Prospective live evaluation"
    assert body["has_settled_data"] is False
    assert "Accumulating data" in body["message"]


def test_model_registry_reflects_a_recorded_promotion(client, db_session):
    from app.models import PlayerModelRun
    from app.player_modelling.model_registry import record_promotion_event

    now = datetime.now(timezone.utc)
    db_session.add(PlayerModelRun(
        model_name="disposals_huber", market="player_disposals", feature_names=[], config_json={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
        evaluation_end_year=2025, is_promoted=True, run_at=now,
    ))
    db_session.commit()
    record_promotion_event(
        db_session, market="player_disposals", previous_champion_model_name="disposals_ridge",
        previous_champion_model_version="disposals_ridge@x", new_champion_model_name="disposals_huber",
        new_champion_model_version="disposals_huber@y", promoted_at=now, evidence_summary="evidence",
        evaluation_metrics={"overall_mae": {"ridge": 3.93, "huber": 3.91}},
    )

    resp = client.get("/api/v1/model-registry")
    body = resp.json()
    assert len(body["promotion_events"]) == 1
    assert body["promotion_events"][0]["new_champion_model_name"] == "disposals_huber"
    assert body["disposal_head_to_head"]["huber"]["status"] == "champion"
