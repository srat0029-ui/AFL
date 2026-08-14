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
        metrics=[{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
                  "holdout_value": 0.205, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )


def test_predictions_404_for_unknown_match(client):
    response = client.get("/api/matches/999999/predictions")
    assert response.status_code == 404


def test_predictions_503_when_models_not_run_yet(client, db_session):
    match = _seed_match(db_session)
    response = client.get(f"/api/matches/{match.id}/predictions")
    assert response.status_code == 503


def test_predictions_available_without_any_odds(client, db_session):
    _seed_model_runs(db_session)
    match = _seed_match(db_session)

    response = client.get(f"/api/matches/{match.id}/predictions")

    assert response.status_code == 200
    body = response.json()
    assert body["match_id"] == match.id
    assert 0.0 <= body["elo_home_win_probability"] <= 1.0
    probs_sum = (
        body["poisson_home_win_probability"] + body["poisson_draw_probability"] + body["poisson_away_win_probability"]
    )
    assert abs(probs_sum - 1.0) < 1e-6
