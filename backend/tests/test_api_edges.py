from datetime import datetime, timezone

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Match, MatchStatus, Round, Season, Sport, Team


def _seed_match(db_session) -> Match:
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    away = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db_session.add_all([round_, home, away])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(2026, 8, 20, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
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
        metrics=[
            {"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
             "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True},
            {"market_type": "total", "metric_name": "mae", "holdout_n": 648,
             "holdout_value": 23.5, "naive_baseline_value": 23.5, "has_edge_over_naive": False},
        ],
    )


def test_edges_404_for_unknown_match(client):
    response = client.get("/api/matches/999999/edges")
    assert response.status_code == 404


def test_edges_503_when_models_not_run_yet(client, db_session):
    match = _seed_match(db_session)
    response = client.get(f"/api/matches/{match.id}/edges")
    assert response.status_code == 503


def test_edges_empty_list_when_no_odds_recorded(client, db_session):
    _seed_model_runs(db_session)
    match = _seed_match(db_session)
    response = client.get(f"/api/matches/{match.id}/edges")
    assert response.status_code == 200
    assert response.json() == []


def test_edges_returns_computed_data_for_recorded_odds(client, db_session):
    _seed_model_runs(db_session)
    match = _seed_match(db_session)
    client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "h2h", "selection": "Carlton", "price_decimal": 1.85},
    )
    client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "h2h", "selection": "Richmond", "price_decimal": 2.05},
    )

    response = client.get(f"/api/matches/{match.id}/edges")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2

    carlton = next(e for e in body if e["selection"] == "Carlton")
    assert carlton["bookmaker_name"] == "Sportsbet"
    assert carlton["overround_removed"] is True
    assert 0.0 < carlton["model_probability"] < 1.0
    assert carlton["confidence_tier"] in ("lower", "moderate", "higher")
    assert carlton["edge_tier"] in ("none", "weak", "moderate", "strong")


def test_edges_total_market_shows_insufficient_data(client, db_session):
    _seed_model_runs(db_session)
    match = _seed_match(db_session)
    client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "total", "selection": "over",
              "line_value": 100.0, "price_decimal": 1.9},
    )

    response = client.get(f"/api/matches/{match.id}/edges")
    body = response.json()
    assert body[0]["confidence_tier"] == "insufficient_data"
