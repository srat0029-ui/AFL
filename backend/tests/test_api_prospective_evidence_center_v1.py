"""API-level tests for /api/v1/prospective-evidence-center — a read-only
composition of the existing prospective datasets (pricing engine, SGM,
real market tracking, Market Monitor). Verifies the composition wires
through correctly and honestly reports empty/accumulating states rather
than fabricating numbers; the underlying calculations themselves are
already covered by each dataset's own existing tests."""

from datetime import datetime, timezone


def test_empty_db_returns_honest_accumulating_states(client, db_session):
    resp = client.get("/api/v1/prospective-evidence-center")
    assert resp.status_code == 200
    body = resp.json()

    assert body["pricing_evaluation"]["dataset_label"] == "Prospective live evaluation"
    assert body["pricing_evaluation"]["has_settled_data"] is False

    assert body["sgm_evaluation"]["dataset_label"] == "SGM prospective live evaluation"
    assert body["sgm_evaluation"]["has_settled_data"] is False

    assert body["real_market_tracking"]["label"] == "Real logged market observations"
    assert body["real_market_tracking"]["summary"]["total_observations"] == 0

    assert body["market_monitor"]["coverage"]["n_frozen_cases"] == 0
    assert body["market_monitor"]["prospective"]["summary"]["n_resolved"] == 0
    assert body["market_monitor"]["retrospective"]["summary"]["n_resolved"] == 0


def test_boundary_inactive_outside_production(client, db_session):
    # The test app runs with app_env="local" by default — no environment
    # variable can silently enable the production-only boundary.
    resp = client.get("/api/v1/prospective-evidence-center")
    body = resp.json()
    assert body["boundary"]["boundary_active"] is False
    assert body["boundary"]["tracking_start_at"] is None
    assert "full available history" in body["boundary"]["environment_note"]


def test_boundary_active_in_production_and_matches_the_fixed_constant(client, db_session, monkeypatch):
    from app.config import Settings
    from app.prospective_boundary import PRODUCTION_PROSPECTIVE_TRACKING_START

    monkeypatch.setattr(
        "app.prospective_boundary.get_settings", lambda: Settings(app_env="production")
    )
    resp = client.get("/api/v1/prospective-evidence-center")
    body = resp.json()
    assert body["boundary"]["boundary_active"] is True
    returned = datetime.fromisoformat(body["boundary"]["tracking_start_at"].replace("Z", "+00:00"))
    assert returned == PRODUCTION_PROSPECTIVE_TRACKING_START


def test_pricing_evaluation_reflects_settled_snapshots_and_market_family_split(client, db_session):
    from app.models import Match, MatchStatus, PricingSnapshot, Round, Season, Sport, Team
    from app.player_modelling.prospective_evaluation import MIN_SAMPLE_FOR_LABELED

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
        db_session.add(PricingSnapshot(
            match_id=match.id, market_family="team", market_type="h2h", selection=home.name,
            model_name="team_elo", model_version="v1", generated_at=now, data_cutoff=now,
            confidence_tier="higher_confidence", model_probability=prob, model_fair_odds=1 / prob,
            outcome=outcome,
        ))
    db_session.commit()

    resp = client.get("/api/v1/prospective-evidence-center")
    body = resp.json()
    pe = body["pricing_evaluation"]
    assert pe["has_settled_data"] is True
    assert pe["n_settled"] == MIN_SAMPLE_FOR_LABELED
    # Genuinely independent sample size, not the raw count — every fixture
    # row here is the same (player_id=None, match_id) team market, so they
    # all collapse to a single unique event even though 30 rows were frozen.
    assert pe["n_unique_player_match_events"] == 1
    team_split = next(s for s in pe["by_market_family"] if s["label"] == "team")
    assert team_split["n_settled"] == MIN_SAMPLE_FOR_LABELED
    assert team_split["exploratory"] is False
