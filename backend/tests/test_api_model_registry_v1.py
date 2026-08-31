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


def test_sgm_prospective_evaluation_empty_db_is_accumulating_state(client, db_session):
    resp = client.get("/api/v1/model-registry/sgm-prospective-evaluation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dataset_label"] == "SGM prospective live evaluation"
    assert body["has_settled_data"] is False
    assert "Accumulating data" in body["message"]


def test_sgm_prospective_evaluation_reports_settled_splits(client, db_session):
    from app.models import Match, MatchStatus, Round, Season, SgmPriceSnapshot, Sport, Team
    from app.player_modelling.sgm_prospective_evaluation import MIN_SAMPLE_FOR_LABELED

    now = datetime.now(timezone.utc)
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db_session.add_all([round_, home, away])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=now, status=MatchStatus.COMPLETED,
    )
    db_session.add(match)
    db_session.flush()
    for i in range(MIN_SAMPLE_FOR_LABELED):
        outcome = "won" if i % 2 == 0 else "lost"
        prob = 0.6 if outcome == "won" else 0.4
        db_session.add(SgmPriceSnapshot(
            match_id=match.id, leg_signature=f"combo-{i}", n_legs=2, leg_type_combination="disposals+h2h",
            snapshot_horizon="24h_plus", hours_to_kickoff=30.0, model_name="sgm_joint_conditional_mc",
            model_version="v1", generated_at=now, model_probability=prob, naive_independence_probability=0.5,
            correlation_adjustment_pp=1.0, model_fair_odds=1 / prob, naive_independence_fair_odds=2.0,
            mc_standard_error=0.003, n_simulations=20000, dependence_validated=True, outcome=outcome,
        ))
    db_session.commit()

    resp = client.get("/api/v1/model-registry/sgm-prospective-evaluation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_settled_data"] is True
    assert body["n_settled"] == MIN_SAMPLE_FOR_LABELED
    assert body["overall"]["exploratory"] is False
    assert body["overall"]["bookmaker_brier"] is None  # never fabricated
    assert len(body["by_snapshot_horizon"]) == 1
    assert body["by_snapshot_horizon"][0]["label"] == "24h_plus"
