from datetime import datetime, timezone

from app.models import Match, MatchStatus, Round, Season, Sport, Team


def _seed_match(db_session, status=MatchStatus.SCHEDULED) -> Match:
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=3)
    home = Team(sport_id=sport.id, name="Carlton", short_name="CAR", primary_colour="#0E1E2D")
    away = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db_session.add_all([round_, home, away])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(2026, 8, 20, tzinfo=timezone.utc), status=status,
        home_score=None, away_score=None,
    )
    db_session.add(match)
    db_session.commit()
    return match


def test_list_matches_returns_seeded_match(client, db_session):
    match = _seed_match(db_session)

    response = client.get("/api/matches")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == match.id
    assert body[0]["home_team"]["name"] == "Carlton"
    assert body[0]["home_team"]["primary_colour"] == "#0E1E2D"
    assert body[0]["away_team"]["name"] == "Richmond"
    assert body[0]["round_number"] == 3
    assert body[0]["season_year"] == 2026


def test_list_matches_filters_by_status(client, db_session):
    _seed_match(db_session, status=MatchStatus.SCHEDULED)

    response = client.get("/api/matches", params={"status": "completed"})

    assert response.status_code == 200
    assert response.json() == []


def test_get_match_by_id(client, db_session):
    match = _seed_match(db_session)

    response = client.get(f"/api/matches/{match.id}")

    assert response.status_code == 200
    assert response.json()["id"] == match.id


def test_get_match_404_for_unknown_id(client):
    response = client.get("/api/matches/999999")
    assert response.status_code == 404
