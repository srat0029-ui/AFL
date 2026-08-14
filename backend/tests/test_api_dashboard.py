from datetime import datetime, timezone

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Match, MatchStatus, Round, Season, Sport, Team


def _seed_match(db_session, round_number=1, status=MatchStatus.SCHEDULED) -> Match:
    sport = db_session.query(Sport).filter_by(code="AFL").first()
    if sport is None:
        sport = Sport(code="AFL", name="Australian Football League")
        db_session.add(sport)
        db_session.flush()
    season = db_session.query(Season).filter_by(sport_id=sport.id, year=2026).first()
    if season is None:
        season = Season(sport_id=sport.id, year=2026)
        db_session.add(season)
        db_session.flush()
    round_ = Round(season_id=season.id, round_number=round_number)
    home = Team(sport_id=sport.id, name=f"Home{round_number}", short_name=f"H{round_number}")
    away = Team(sport_id=sport.id, name=f"Away{round_number}", short_name=f"A{round_number}")
    db_session.add_all([round_, home, away])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(2026, 8, 20 + round_number, tzinfo=timezone.utc), status=status,
    )
    db_session.add(match)
    db_session.commit()
    return match


def _seed_model_runs(db_session) -> None:
    persist_model_run(
        db_session, "elo", EloConfig(), 2022,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
                  "holdout_value": 0.20, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_model_run(
        db_session, "poisson", PoissonConfig(), 2022,
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
                  "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )


def test_dashboard_empty_when_no_upcoming_matches(client, db_session):
    _seed_model_runs(db_session)
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    assert response.json() == []


def test_dashboard_shows_matches_without_predictions_when_models_not_run(client, db_session):
    match = _seed_match(db_session)
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["match"]["id"] == match.id
    assert body[0]["predictions"] is None
    assert body[0]["best_edge"] is None


def test_dashboard_lists_upcoming_matches_with_predictions(client, db_session):
    _seed_model_runs(db_session)
    _seed_match(db_session, round_number=1)
    _seed_match(db_session, round_number=2)

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    for entry in body:
        assert entry["predictions"] is not None
        assert entry["best_edge"] is None  # no odds entered for either match


def test_dashboard_excludes_completed_matches(client, db_session):
    _seed_model_runs(db_session)
    _seed_match(db_session, round_number=1, status=MatchStatus.COMPLETED)
    _seed_match(db_session, round_number=2, status=MatchStatus.SCHEDULED)

    response = client.get("/api/dashboard")
    body = response.json()
    assert len(body) == 1
    assert body[0]["match"]["status"] == "scheduled"


def test_dashboard_includes_best_edge_when_odds_recorded(client, db_session):
    _seed_model_runs(db_session)
    match = _seed_match(db_session, round_number=1)
    client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "h2h", "selection": "Home1", "price_decimal": 1.85},
    )

    response = client.get("/api/dashboard")
    body = response.json()
    entry = next(e for e in body if e["match"]["id"] == match.id)
    assert entry["best_edge"] is not None
    assert entry["best_edge"]["selection"] == "Home1"
