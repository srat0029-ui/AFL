"""API-level tests for the versioned B2B pricing endpoints: health,
model-health, and a match-pricing round trip returning valid probabilities."""

from datetime import datetime, timedelta, timezone

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Match, MatchStatus, Round, Season, Sport, Team

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
