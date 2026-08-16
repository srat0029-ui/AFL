from datetime import datetime, timezone

from sqlalchemy import select

from app.models import (
    Bookmaker,
    ExpectedLineup,
    Match,
    MatchStatus,
    Player,
    PlayerDisposalProjection,
    PlayerModelRun,
    PlayerPropMarket,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.market import PlayerMarket

NOW = datetime.now(timezone.utc)


def _seed(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW, status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    db.flush()
    db.add(
        PlayerModelRun(
            model_name="disposals_ridge", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
            distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
            evaluation_end_year=2025, is_promoted=True, run_at=NOW,
        )
    )
    db.add(
        PlayerDisposalProjection(
            match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposals_ridge", model_version="v1",
            generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
            predicted_mean=28.0, distribution_method="nb", nb_alpha=3.0, confidence_tier="higher_confidence",
            warnings=[], input_features={},
        )
    )
    db.add(ExpectedLineup(match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in", selection_status="confirmed_selected", is_confirmed=True, recorded_at=NOW, source="manual"))
    bookmaker = Bookmaker(name="Sportsbet")
    db.add(bookmaker)
    db.flush()
    db.add(
        PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
            line_type="over_under", threshold=27.5, selection="over", price_decimal=1.9, recorded_at=NOW, source="the_odds_api",
        )
    )
    db.commit()
    return match


def test_normalized_prop_insights_endpoint_returns_row(client, db_session):
    match = _seed(db_session)
    resp = client.get("/api/afl/prop-insights/normalized")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["match_id"] == match.id
    assert body[0]["best_bookmaker"] == "Sportsbet"
    assert "opportunity_score" in body[0]
    assert "bookmakers" in body[0]
    assert "price_movement" in body[0]


def test_normalized_prop_insights_market_filter(client, db_session):
    _seed(db_session)
    resp = client.get("/api/afl/prop-insights/normalized?market=player_goals")
    assert resp.status_code == 200
    assert resp.json() == []


def test_normalized_prop_insights_match_id_filter(client, db_session):
    match = _seed(db_session)
    resp = client.get(f"/api/afl/prop-insights/normalized?match_id={match.id}")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    resp2 = client.get(f"/api/afl/prop-insights/normalized?match_id={match.id + 999}")
    assert resp2.json() == []
