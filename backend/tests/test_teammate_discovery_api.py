"""API-level tests for the teammate discovery (context-candidates)
endpoint, exercised through the real FastAPI TestClient (see
tests/conftest.py).
"""

from datetime import datetime, timedelta, timezone

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team

BASE = datetime(2024, 3, 1, tzinfo=timezone.utc)


def _seed_scenario(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2024)
    db.add(season)
    db.flush()
    home = Team(sport_id=sport.id, name="North Melbourne", short_name="NM")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([home, away])
    db.flush()
    player = Player(sport_id=sport.id, display_name="Harry Sheezel", source="afltables", source_player_id="players/S/Sheezel.html")
    teammate = Player(sport_id=sport.id, display_name="Luke Davies-Uniacke", source="afltables", source_player_id="players/D/Davies-Uniacke.html")
    db.add_all([player, teammate])
    db.commit()

    start = BASE
    for i, (d, teammate_in) in enumerate([(20, True), (22, True), (24, True), (30, False), (32, False), (34, False)]):
        round_ = Round(season_id=season.id, round_number=i + 1)
        db.add(round_)
        db.flush()
        match = Match(
            sport_id=sport.id, season_id=season.id, round_id=round_.id,
            home_team_id=home.id, away_team_id=away.id, scheduled_start=start, status=MatchStatus.COMPLETED,
        )
        db.add(match)
        db.flush()
        db.add(PlayerMatchStat(
            player_id=player.id, match_id=match.id, team_id=home.id, opponent_team_id=away.id,
            source="afltables", recorded_at=start, disposals=d, goals=1, time_on_ground_pct=85,
        ))
        if teammate_in:
            db.add(PlayerMatchStat(
                player_id=teammate.id, match_id=match.id, team_id=home.id, opponent_team_id=away.id,
                source="afltables", recorded_at=start, disposals=15, goals=0, time_on_ground_pct=88,
            ))
        db.commit()
        start += timedelta(days=7)

    return player, teammate


def test_get_teammate_discovery_returns_candidate_shape(client, db_session):
    player, teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/context-candidates")
    assert resp.status_code == 200
    body = resp.json()

    assert body["player_id"] == player.id
    assert body["stat"] == "disposals"
    assert len(body["candidates"]) == 1
    candidate = body["candidates"][0]
    assert candidate["teammate_id"] == teammate.id
    assert candidate["teammate_name"] == "Luke Davies-Uniacke"
    assert candidate["with_teammate"]["games"] == 3
    assert candidate["without_teammate"]["games"] == 3
    assert "sufficient_evidence" in candidate
    assert "confidence" in candidate
    assert "adjusted_effect" in candidate


def test_get_teammate_discovery_supports_goals_stat(client, db_session):
    player, _teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/context-candidates", params={"stat": "goals"})
    assert resp.status_code == 200
    assert resp.json()["stat"] == "goals"


def test_get_teammate_discovery_rejects_unsupported_stat(client, db_session):
    player, _teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/context-candidates", params={"stat": "tackles"})
    assert resp.status_code == 400


def test_get_teammate_discovery_rejects_malformed_thresholds(client, db_session):
    player, _teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/context-candidates", params={"thresholds": "abc"})
    assert resp.status_code == 400


def test_get_teammate_discovery_404_for_unknown_player(client, db_session):
    resp = client.get("/api/afl/players/999999/context-candidates")
    assert resp.status_code == 404


def test_get_teammate_discovery_empty_candidates_when_no_teammates_recorded(client, db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2024)
    db_session.add(season)
    db_session.flush()
    home = Team(sport_id=sport.id, name="North Melbourne", short_name="NM")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db_session.add_all([home, away])
    db_session.flush()
    player = Player(sport_id=sport.id, display_name="Solo Player", source="afltables", source_player_id="players/Z/Z.html")
    db_session.add(player)
    db_session.commit()

    round_ = Round(season_id=season.id, round_number=1)
    db_session.add(round_)
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id, scheduled_start=BASE, status=MatchStatus.COMPLETED,
    )
    db_session.add(match)
    db_session.flush()
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, opponent_team_id=away.id,
        source="afltables", recorded_at=BASE, disposals=20, goals=1, time_on_ground_pct=85,
    ))
    db_session.commit()

    resp = client.get(f"/api/afl/players/{player.id}/context-candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidates"] == []
    assert "No other player" in body["explanation"]
