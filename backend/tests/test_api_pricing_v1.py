"""API-level tests for the versioned B2B pricing endpoints: health,
model-health, and a match-pricing round trip returning valid probabilities."""

from datetime import datetime, timedelta, timezone

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Match, MatchStatus, Player, PlayerGoalProjection, Round, Season, Sport, Team

NOW = datetime.now(timezone.utc)


def _seed(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    away = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    persist_model_run(db, "elo", EloConfig(), 2022, metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 10, "holdout_value": 0.2, "naive_baseline_value": 0.25, "has_edge_over_naive": True}])
    persist_model_run(db, "poisson", PoissonConfig(), 2022, metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 10, "holdout_value": 0.2, "naive_baseline_value": 0.25, "has_edge_over_naive": True}])
    return match


def test_pricing_health(client, db_session):
    resp = client.get("/api/v1/pricing/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_model_health_reports_promotion_state(client, db_session):
    resp = client.get("/api/v1/pricing/model-health")
    assert resp.status_code == 200
    names = {m["model_name"] for m in resp.json()["models"]}
    assert names == {"disposal_nb", "goal_hurdle", "elo_poisson"}


def test_match_pricing_round_trip_returns_valid_probabilities(client, db_session):
    match = _seed(db_session)

    resp = client.get(f"/api/v1/pricing/afl/matches/{match.id}")

    assert resp.status_code == 200
    body = resp.json()
    team = body["team"]
    assert 0.0 <= team["home_win_probability"] <= 1.0
    total = team["home_win_probability"] + team["draw_probability"] + team["away_win_probability"]
    assert abs(total - 1.0) < 1e-6
    assert team["provenance"]["model_name"] == "elo_poisson"
    assert team["provenance"]["model_version"]
    assert body["disposals"] == []  # no persisted projections seeded for this test
    assert body["goals"] == []


def test_match_pricing_missing_match_is_404(client, db_session):
    assert client.get("/api/v1/pricing/afl/matches/999999").status_code == 404


def test_current_round_pricing_returns_empty_when_no_upcoming_matches(client, db_session):
    resp = client.get("/api/v1/pricing/afl/current-round")
    assert resp.status_code == 200
    assert resp.json()["n_matches"] == 0


def test_goal_price_response_carries_structured_model_risk_flag(client, db_session):
    match = _seed(db_session)
    home = db_session.get(Team, match.home_team_id)
    player = Player(sport_id=home.sport_id, display_name="Flagged Goalkicker", source="afltables", source_player_id="flagged-1", current_team_id=home.id)
    db_session.add(player)
    db_session.flush()
    db_session.add(PlayerGoalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id, model_name="goal_hurdle", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=20,
        predicted_mean=0.9, distribution_kind="hurdle", p_score=0.6, mu_scored=1.5, alpha_scored=0.4,
        scoring_archetype="forward", confidence_tier="higher_confidence", warnings=[], input_features={},
        usage_regime="changed", usage_change_score=2.4,
    ))
    db_session.commit()

    resp = client.get(f"/api/v1/pricing/afl/matches/{match.id}")

    assert resp.status_code == 200
    goal = resp.json()["goals"][0]
    assert goal["usage_regime"] == "changed"
    assert goal["usage_change_score"] == 2.4
    assert goal["model_risk_flags"] == [
        {
            "code": "RECENT_USAGE_REGIME_CHANGE",
            "description": (
                "Recent usage profile materially differs from the player's established baseline. "
                "Historically, goal point-error was approximately 11% higher in this state."
            ),
        }
    ]


def test_same_game_multi_round_trip(client, db_session):
    from app.models import PlayerDisposalProjection

    match = _seed(db_session)
    home = db_session.get(Team, match.home_team_id)
    player = Player(sport_id=home.sport_id, display_name="Same Game Player", source="afltables", source_player_id="sgm-1", current_team_id=home.id)
    db_session.add(player)
    db_session.flush()
    db_session.add(PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposal_nb", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=30,
        predicted_mean=22.0, distribution_method="nb", nb_alpha=0.15, confidence_tier="higher_confidence",
        warnings=[], input_features={},
    ))
    db_session.commit()

    resp = client.post("/api/v1/pricing/afl/same-game", json={
        "match_id": match.id,
        "legs": [
            {"leg_type": "h2h", "team_id": home.id},
            {"leg_type": "disposals", "player_id": player.id, "threshold": 21.5},
        ],
        "n_simulations": 20000,
    })

    assert resp.status_code == 200
    body = resp.json()
    assert 0.0 < body["model_probability"] < 1.0
    assert body["model_fair_odds"] > 1.0
    assert len(body["legs"]) == 2
    assert body["provenance"]["model_name"] == "sgm_joint_conditional_mc"
    assert body["dependence_validated"] is False  # no SgmDependenceCoefficient row seeded


def test_same_game_multi_rejects_multiple_team_legs(client, db_session):
    from app.models import PlayerDisposalProjection

    match = _seed(db_session)
    home = db_session.get(Team, match.home_team_id)
    player = Player(sport_id=home.sport_id, display_name="Correlation Test Player", source="afltables", source_player_id="sgm-2", current_team_id=home.id)
    db_session.add(player)
    db_session.flush()
    db_session.add(PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposal_nb", model_version="v1",
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=30,
        predicted_mean=22.0, distribution_method="nb", nb_alpha=0.15, confidence_tier="higher_confidence",
        warnings=[], input_features={},
    ))
    db_session.commit()

    resp = client.post("/api/v1/pricing/afl/same-game", json={
        "match_id": match.id,
        "legs": [
            {"leg_type": "h2h", "team_id": home.id},
            {"leg_type": "line", "team_id": home.id, "line_value": -12.5},
            {"leg_type": "disposals", "player_id": player.id, "threshold": 21.5},
        ],
    })

    assert resp.status_code == 400
