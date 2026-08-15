from datetime import datetime, timezone

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team


def _seed(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season_2023 = Season(sport_id=sport.id, year=2023)
    season_2024 = Season(sport_id=sport.id, year=2024)
    db_session.add_all([season_2023, season_2024])
    db_session.flush()
    round1_2023 = Round(season_id=season_2023.id, round_number=1)
    round1 = Round(season_id=season_2024.id, round_number=1)
    round2 = Round(season_id=season_2024.id, round_number=2)
    db_session.add_all([round1_2023, round1, round2])
    carlton = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    richmond = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db_session.add_all([carlton, richmond])
    db_session.flush()

    m0 = Match(
        sport_id=sport.id, season_id=season_2023.id, round_id=round1_2023.id,
        home_team_id=carlton.id, away_team_id=richmond.id,
        scheduled_start=datetime(2023, 3, 1, tzinfo=timezone.utc), status=MatchStatus.COMPLETED,
        home_score=90, away_score=80,
    )
    m1 = Match(
        sport_id=sport.id, season_id=season_2024.id, round_id=round1.id,
        home_team_id=carlton.id, away_team_id=richmond.id,
        scheduled_start=datetime(2024, 3, 1, tzinfo=timezone.utc), status=MatchStatus.COMPLETED,
        home_score=90, away_score=80,
    )
    m2 = Match(
        sport_id=sport.id, season_id=season_2024.id, round_id=round2.id,
        home_team_id=richmond.id, away_team_id=carlton.id,
        scheduled_start=datetime(2024, 3, 8, tzinfo=timezone.utc), status=MatchStatus.COMPLETED,
        home_score=70, away_score=95,
    )
    db_session.add_all([m0, m1, m2])
    db_session.flush()

    blake = Player(
        sport_id=sport.id, display_name="Blake Acres", current_team_id=carlton.id,
        source="afltables", source_player_id="players/B/Blake_Acres.html", is_active=True,
    )
    jack = Player(
        sport_id=sport.id, display_name="Jack Riewoldt", current_team_id=richmond.id,
        source="afltables", source_player_id="players/J/Jack_Riewoldt.html", is_active=True,
    )
    db_session.add_all([blake, jack])
    db_session.flush()

    stat0 = PlayerMatchStat(
        player_id=blake.id, match_id=m0.id, team_id=carlton.id, opponent_team_id=richmond.id,
        source="afltables", recorded_at=datetime.now(timezone.utc), disposals=18, kicks=10, handballs=8, goals=1,
    )
    stat1 = PlayerMatchStat(
        player_id=blake.id, match_id=m1.id, team_id=carlton.id, opponent_team_id=richmond.id,
        source="afltables", recorded_at=datetime.now(timezone.utc), disposals=25, kicks=16, handballs=9, goals=1, time_on_ground_pct=84,
    )
    stat2 = PlayerMatchStat(
        player_id=blake.id, match_id=m2.id, team_id=carlton.id, opponent_team_id=richmond.id,
        source="afltables", recorded_at=datetime.now(timezone.utc), disposals=20, kicks=12, handballs=8, goals=0, subbed_off=True,
    )
    stat3 = PlayerMatchStat(
        player_id=jack.id, match_id=m1.id, team_id=richmond.id, opponent_team_id=carlton.id,
        source="afltables", recorded_at=datetime.now(timezone.utc), disposals=14, kicks=9, handballs=5, goals=3,
    )
    db_session.add_all([stat0, stat1, stat2, stat3])
    db_session.commit()
    return {
        "carlton": carlton, "richmond": richmond, "blake": blake, "jack": jack,
        "m0": m0, "m1": m1, "m2": m2,
    }


def test_list_players_returns_all(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/players")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {p["display_name"] for p in body["players"]} == {"Blake Acres", "Jack Riewoldt"}


def test_list_players_filters_by_team(client, db_session):
    seed = _seed(db_session)
    response = client.get(f"/api/afl/players?team_id={seed['carlton'].id}")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["players"][0]["display_name"] == "Blake Acres"


def test_list_players_filters_by_name_search(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/players?name=riewoldt")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["players"][0]["display_name"] == "Jack Riewoldt"


def test_list_players_filters_by_season(client, db_session):
    seed = _seed(db_session)
    response = client.get("/api/afl/players?season=2023")
    assert response.status_code == 200
    body = response.json()
    # only Blake played in the 2023-seeded match
    assert body["total"] == 1
    assert body["players"][0]["id"] == seed["blake"].id


def test_list_players_pagination(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/players?limit=1&offset=0")
    body = response.json()
    assert len(body["players"]) == 1
    assert body["total"] == 2
    assert body["limit"] == 1
    assert body["offset"] == 0


def test_get_player_by_id(client, db_session):
    seed = _seed(db_session)
    response = client.get(f"/api/afl/players/{seed['blake'].id}")
    assert response.status_code == 200
    assert response.json()["display_name"] == "Blake Acres"


def test_get_player_404(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/players/999999")
    assert response.status_code == 404


def test_player_games_returns_full_history_sorted_desc(client, db_session):
    seed = _seed(db_session)
    response = client.get(f"/api/afl/players/{seed['blake'].id}/games")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    dates = [g["scheduled_start"] for g in body["games"]]
    assert dates == sorted(dates, reverse=True)
    assert body["games"][0]["disposals"] == 20  # most recent (m2)


def test_player_games_filters_by_season(client, db_session):
    seed = _seed(db_session)
    response = client.get(f"/api/afl/players/{seed['blake'].id}/games?season=2024")
    body = response.json()
    assert body["total"] == 2


def test_player_games_404_for_unknown_player(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/players/999999/games")
    assert response.status_code == 404


def test_player_form_returns_season_averages_and_recent_games(client, db_session):
    seed = _seed(db_session)
    response = client.get(f"/api/afl/players/{seed['blake'].id}/form")
    assert response.status_code == 200
    body = response.json()
    assert len(body["recent_games"]) == 3
    by_year = {s["season_year"]: s for s in body["season_averages"]}
    assert by_year[2024]["games_played"] == 2
    assert by_year[2024]["averages"]["disposals"] == (25 + 20) / 2
    assert by_year[2023]["games_played"] == 1


def test_player_form_respects_recent_games_limit(client, db_session):
    seed = _seed(db_session)
    response = client.get(f"/api/afl/players/{seed['blake'].id}/form?recent_games=2")
    body = response.json()
    assert len(body["recent_games"]) == 2


def test_match_players_splits_by_team(client, db_session):
    seed = _seed(db_session)
    response = client.get(f"/api/afl/matches/{seed['m1'].id}/players")
    assert response.status_code == 200
    body = response.json()
    assert len(body["home_team_players"]) == 1
    assert body["home_team_players"][0]["team"]["name"] == "Carlton"
    assert body["home_team_players"][0]["player_display_name"] == "Blake Acres"
    assert body["home_team_players"][0]["player_id"] == seed["blake"].id
    assert len(body["away_team_players"]) == 1
    assert body["away_team_players"][0]["team"]["name"] == "Richmond"
    assert body["away_team_players"][0]["player_display_name"] == "Jack Riewoldt"


def test_match_players_404_for_unknown_match(client, db_session):
    _seed(db_session)
    response = client.get("/api/afl/matches/999999/players")
    assert response.status_code == 404
