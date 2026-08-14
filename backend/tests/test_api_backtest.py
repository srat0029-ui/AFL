from datetime import datetime, timezone

from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.models import Match, MatchStatus, Round, Season, Sport, Team


def _seed_completed_match(db_session) -> Match:
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2024)
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
        scheduled_start=datetime(2024, 3, 1, tzinfo=timezone.utc), status=MatchStatus.COMPLETED,
        home_score=90, away_score=80, home_goals=13, home_behinds=12, away_goals=11, away_behinds=14,
    )
    db_session.add(match)
    db_session.commit()
    return match


def _seed_model_runs(db_session):
    persist_model_run(db_session, "elo", EloConfig(), 2023, metrics=[])
    persist_model_run(db_session, "poisson", PoissonConfig(), 2023, metrics=[])


def test_backtest_503_when_models_not_run(client, db_session):
    _seed_completed_match(db_session)
    response = client.get("/api/backtest")
    assert response.status_code == 503


def test_backtest_returns_full_overview(client, db_session):
    _seed_completed_match(db_session)
    _seed_model_runs(db_session)

    response = client.get("/api/backtest")

    assert response.status_code == 200
    body = response.json()
    assert body["elo"]["model_name"] == "elo"
    assert body["elo"]["overall"]["n"] == 1
    assert body["poisson_win"]["model_name"] == "poisson"
    assert "total_points_mae" in body["poisson_scoring"]["overall"]["metrics"]
    assert len(body["elo"]["calibration"]) == 10
    assert body["logged_odds"]["n_total"] == 0
    assert body["logged_odds"]["win_rate"] is None


def test_backtest_logged_odds_reflects_resolved_selection(client, db_session):
    match = _seed_completed_match(db_session)
    _seed_model_runs(db_session)

    # log an odds quote directly for the (already completed) match, simulating
    # a selection that was tracked before the match and has since resolved
    from sqlalchemy import select

    from app.models import Bookmaker, OddsQuote

    bookmaker = Bookmaker(name="Sportsbet")
    db_session.add(bookmaker)
    db_session.flush()
    db_session.add(
        OddsQuote(
            match_id=match.id, bookmaker_id=bookmaker.id, market_type="h2h", selection="Carlton",
            price_decimal=1.80, recorded_at=datetime.now(timezone.utc), source="manual", is_closing_line=False,
        )
    )
    db_session.commit()

    response = client.get("/api/backtest")
    body = response.json()

    assert body["logged_odds"]["n_total"] == 1
    assert body["logged_odds"]["n_resolved"] == 1
    assert body["logged_odds"]["win_rate"] == 1.0
    assert body["logged_odds"]["selections"][0]["won"] is True
