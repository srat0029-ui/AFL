from datetime import datetime, timezone

from app.models import Match, MatchStatus, Round, Season, Sport, Team


def _seed_match(client, db_session) -> Match:
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=3)
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


def test_create_h2h_odds_quote(client, db_session):
    match = _seed_match(client, db_session)

    response = client.post(
        f"/api/matches/{match.id}/odds",
        json={
            "bookmaker_name": "Sportsbet",
            "market_type": "h2h",
            "selection": "Carlton",
            "price_decimal": 1.85,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["bookmaker_name"] == "Sportsbet"
    assert body["market_type"] == "h2h"
    assert body["selection"] == "Carlton"
    assert body["price_decimal"] == 1.85
    assert body["line_value"] is None
    assert body["source"] == "manual"


def test_create_line_odds_requires_line_value(client, db_session):
    match = _seed_match(client, db_session)

    response = client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "TAB", "market_type": "line", "selection": "Carlton", "price_decimal": 1.9},
    )

    assert response.status_code == 422  # pydantic validation failure


def test_create_line_odds_with_line_value_succeeds(client, db_session):
    match = _seed_match(client, db_session)

    response = client.post(
        f"/api/matches/{match.id}/odds",
        json={
            "bookmaker_name": "TAB",
            "market_type": "line",
            "selection": "Carlton",
            "line_value": -12.5,
            "price_decimal": 1.9,
        },
    )

    assert response.status_code == 201
    assert response.json()["line_value"] == -12.5


def test_create_total_odds_requires_over_under_selection(client, db_session):
    match = _seed_match(client, db_session)

    response = client.post(
        f"/api/matches/{match.id}/odds",
        json={
            "bookmaker_name": "Ladbrokes",
            "market_type": "total",
            "selection": "Carlton",
            "line_value": 165.5,
            "price_decimal": 1.9,
        },
    )

    assert response.status_code == 422


def test_create_total_odds_accepts_over_under(client, db_session):
    match = _seed_match(client, db_session)

    response = client.post(
        f"/api/matches/{match.id}/odds",
        json={
            "bookmaker_name": "Ladbrokes",
            "market_type": "total",
            "selection": "over",
            "line_value": 165.5,
            "price_decimal": 1.9,
        },
    )

    assert response.status_code == 201


def test_create_h2h_odds_rejects_unknown_team_selection(client, db_session):
    match = _seed_match(client, db_session)

    response = client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "h2h", "selection": "Essendon", "price_decimal": 1.85},
    )

    assert response.status_code == 400


def test_create_odds_rejects_price_at_or_below_one(client, db_session):
    match = _seed_match(client, db_session)

    response = client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "h2h", "selection": "Carlton", "price_decimal": 1.0},
    )

    assert response.status_code == 422


def test_create_odds_for_unknown_match_returns_404(client):
    response = client.post(
        "/api/matches/999999/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "h2h", "selection": "Carlton", "price_decimal": 1.85},
    )
    assert response.status_code == 404


def test_list_odds_for_match(client, db_session):
    match = _seed_match(client, db_session)
    client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "h2h", "selection": "Carlton", "price_decimal": 1.85},
    )
    client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "TAB", "market_type": "h2h", "selection": "Richmond", "price_decimal": 2.1},
    )

    response = client.get(f"/api/matches/{match.id}/odds")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert {q["bookmaker_name"] for q in body} == {"Sportsbet", "TAB"}


def test_delete_odds_quote(client, db_session):
    match = _seed_match(client, db_session)
    created = client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "h2h", "selection": "Carlton", "price_decimal": 1.85},
    ).json()

    delete_response = client.delete(f"/api/odds/{created['id']}")
    assert delete_response.status_code == 204

    list_response = client.get(f"/api/matches/{match.id}/odds")
    assert list_response.json() == []


def test_delete_unknown_odds_quote_returns_404(client):
    response = client.delete("/api/odds/999999")
    assert response.status_code == 404


def test_bookmaker_reused_across_quotes_not_duplicated(client, db_session):
    match = _seed_match(client, db_session)
    client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "h2h", "selection": "Carlton", "price_decimal": 1.85},
    )
    client.post(
        f"/api/matches/{match.id}/odds",
        json={"bookmaker_name": "Sportsbet", "market_type": "h2h", "selection": "Richmond", "price_decimal": 2.0},
    )

    response = client.get("/api/bookmakers")
    assert response.json() == ["Sportsbet"]


def test_list_bookmakers_empty_initially(client):
    response = client.get("/api/bookmakers")
    assert response.status_code == 200
    assert response.json() == []
