"""API-level tests for /api/v1/market-movement/* — endpoint wiring, 404
handling for unknown matches/players, and honest empty states. The
underlying analytical behaviour is covered in depth by
test_market_movement_series.py; these tests focus on the HTTP surface."""

from datetime import datetime, timedelta, timezone

from app.models import Bookmaker, Match, MatchStatus, OddsQuote, Round, Season, Sport, Team

KICKOFF = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _seed_match_with_quote(db):
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
        scheduled_start=KICKOFF, status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    tab = Bookmaker(name="TAB")
    db.add(tab)
    db.flush()
    db.add(OddsQuote(match_id=match.id, bookmaker_id=tab.id, market_type="h2h", selection=home.name, line_value=None, price_decimal=1.90, recorded_at=KICKOFF - timedelta(hours=5), source="manual", is_closing_line=False))
    db.commit()
    return match, home, away


def test_matches_endpoint_empty_db_returns_empty_list(client, db_session):
    resp = client.get("/api/v1/market-movement/matches")
    assert resp.status_code == 200
    assert resp.json() == []


def test_matches_endpoint_reflects_seeded_history(client, db_session):
    match, home, away = _seed_match_with_quote(db_session)
    resp = client.get("/api/v1/market-movement/matches")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["match"]["match_id"] == match.id
    assert body[0]["n_odds_quotes"] == 1


def test_market_options_unknown_match_is_404(client, db_session):
    resp = client.get("/api/v1/market-movement/matches/999999/markets")
    assert resp.status_code == 404


def test_market_options_lists_the_seeded_h2h_market(client, db_session):
    match, home, away = _seed_match_with_quote(db_session)
    resp = client.get(f"/api/v1/market-movement/matches/{match.id}/markets")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["team_markets"]) == 1
    assert body["team_markets"][0]["market_type"] == "h2h"
    assert body["team_markets"][0]["selection"] == home.name


def test_series_endpoint_team_identity_returns_data(client, db_session):
    match, home, away = _seed_match_with_quote(db_session)
    resp = client.get(
        "/api/v1/market-movement/series",
        params={"match_id": match.id, "identity_type": "team", "market_type": "h2h", "selection": home.name},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["bookmaker_quotes"]) == 1
    assert body["bookmaker_quotes"][0]["bookmaker_name"] == "TAB"
    assert "official closing" not in " ".join(body["methodology_notes"]).lower()


def test_series_endpoint_unknown_match_is_404(client, db_session):
    resp = client.get(
        "/api/v1/market-movement/series",
        params={"match_id": 999999, "identity_type": "team", "market_type": "h2h", "selection": "Collingwood"},
    )
    assert resp.status_code == 404


def test_series_endpoint_team_without_selection_is_422(client, db_session):
    match, home, away = _seed_match_with_quote(db_session)
    resp = client.get(
        "/api/v1/market-movement/series",
        params={"match_id": match.id, "identity_type": "team", "market_type": "h2h"},
    )
    assert resp.status_code == 422


def test_series_endpoint_player_missing_required_params_is_422(client, db_session):
    match, home, away = _seed_match_with_quote(db_session)
    resp = client.get(
        "/api/v1/market-movement/series",
        params={"match_id": match.id, "identity_type": "player", "market_type": "player_disposals"},
    )
    assert resp.status_code == 422


def test_series_endpoint_no_history_market_returns_honest_empty_series(client, db_session):
    match, home, away = _seed_match_with_quote(db_session)
    resp = client.get(
        "/api/v1/market-movement/series",
        params={"match_id": match.id, "identity_type": "team", "market_type": "h2h", "selection": away.name},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bookmaker_quotes"] == []
    assert body["bookmaker_summary"]["insufficient_history"] is True
