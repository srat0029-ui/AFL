from datetime import datetime, timezone

from app.models import Match, MatchStatus, Round, Season, Sport, Team


def _seed(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season_2024 = Season(sport_id=sport.id, year=2024)
    season_2025 = Season(sport_id=sport.id, year=2025)
    db_session.add_all([season_2024, season_2025])
    db_session.flush()
    round1 = Round(season_id=season_2024.id, round_number=1)
    round2 = Round(season_id=season_2024.id, round_number=2)
    db_session.add_all([round1, round2])
    carlton = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    richmond = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    essendon = Team(sport_id=sport.id, name="Essendon", short_name="ESS")
    db_session.add_all([carlton, richmond, essendon])
    db_session.flush()
    m1 = Match(
        sport_id=sport.id, season_id=season_2024.id, round_id=round1.id,
        home_team_id=carlton.id, away_team_id=richmond.id,
        scheduled_start=datetime(2024, 3, 1, tzinfo=timezone.utc), status=MatchStatus.COMPLETED,
        home_score=90, away_score=80,
    )
    m2 = Match(
        sport_id=sport.id, season_id=season_2024.id, round_id=round2.id,
        home_team_id=essendon.id, away_team_id=carlton.id,
        scheduled_start=datetime(2024, 3, 8, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db_session.add_all([m1, m2])
    db_session.commit()
    return {
        "carlton": carlton, "richmond": richmond, "essendon": essendon,
        "season_2024": season_2024, "season_2025": season_2025, "m1": m1, "m2": m2,
    }


def test_list_teams(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/teams")
    assert response.status_code == 200
    assert {t["name"] for t in response.json()} == {"Carlton", "Richmond", "Essendon"}


def test_list_seasons(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/seasons")
    assert response.status_code == 200
    assert [s["year"] for s in response.json()] == [2024, 2025]


def test_list_matches_filters_by_season(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/matches", params={"season": 2024})
    assert len(response.json()) == 2

    response = client.get("/api/afl/matches", params={"season": 2025})
    assert response.json() == []


def test_list_matches_filters_by_team(client, db_session):
    seed = _seed(db_session)
    response = client.get("/api/afl/matches", params={"team_id": seed["richmond"].id})
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == seed["m1"].id


def test_list_matches_filters_by_status(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/matches", params={"status": "completed"})
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "completed"


def test_list_matches_filters_by_round(client, db_session):
    seed = _seed(db_session)
    response = client.get("/api/afl/matches", params={"round_number": 2})
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == seed["m2"].id


def test_list_matches_filters_are_composable(client, db_session):
    seed = _seed(db_session)
    # Carlton plays in both matches; combined with status=completed it should narrow to just m1.
    response = client.get(
        "/api/afl/matches", params={"team_id": seed["carlton"].id, "status": "completed"}
    )
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == seed["m1"].id


def test_list_matches_order_and_limit(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/matches", params={"order": "desc", "limit": 1})
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "scheduled"  # later scheduled_start


def test_matches_upcoming_endpoint(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/matches/upcoming")
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "scheduled"


def test_get_afl_match_by_id(client, db_session):
    seed = _seed(db_session)
    response = client.get(f"/api/afl/matches/{seed['m1'].id}")
    assert response.status_code == 200
    assert response.json()["id"] == seed["m1"].id


def test_get_afl_match_404(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/matches/99999")
    assert response.status_code == 404


def test_season_matches_endpoint(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/seasons/2024/matches")
    assert len(response.json()) == 2

    response = client.get("/api/afl/seasons/2025/matches")
    assert response.json() == []


def test_season_matches_endpoint_composes_with_filters(client, db_session):
    seed = _seed(db_session)
    response = client.get("/api/afl/seasons/2024/matches", params={"round_number": 2})
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == seed["m2"].id


def test_legacy_matches_endpoint_still_works(client, db_session):
    _seed(db_session)
    response = client.get("/api/matches")
    assert response.status_code == 200
    assert len(response.json()) == 2
