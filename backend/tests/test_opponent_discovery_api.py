"""API-level tests for the opponent discovery (opponent-context-candidates)
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
    opp_a = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    opp_b = Team(sport_id=sport.id, name="Essendon", short_name="ESS")
    db.add_all([home, opp_a, opp_b])
    db.flush()
    player = Player(sport_id=sport.id, display_name="Harry Sheezel", source="afltables", source_player_id="players/S/Sheezel.html")
    db.add(player)
    db.commit()

    start = BASE
    for i, (d, opponent) in enumerate([(20, opp_a), (22, opp_a), (24, opp_a), (30, opp_b), (32, opp_b), (34, opp_b)]):
        round_ = Round(season_id=season.id, round_number=i + 1)
        db.add(round_)
        db.flush()
        match = Match(
            sport_id=sport.id, season_id=season.id, round_id=round_.id,
            home_team_id=home.id, away_team_id=opponent.id, scheduled_start=start, status=MatchStatus.COMPLETED,
        )
        db.add(match)
        db.flush()
        db.add(PlayerMatchStat(
            player_id=player.id, match_id=match.id, team_id=home.id, opponent_team_id=opponent.id,
            source="afltables", recorded_at=start, disposals=d, goals=1, time_on_ground_pct=85,
        ))
        db.commit()
        start += timedelta(days=7)

    return player, opp_a, opp_b


def test_get_opponent_discovery_returns_candidate_shape(client, db_session):
    player, opp_a, opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/opponent-context-candidates")
    assert resp.status_code == 200
    body = resp.json()

    assert body["player_id"] == player.id
    assert body["stat"] == "disposals"
    assert len(body["candidates"]) == 2
    ids = {c["opponent_team_id"] for c in body["candidates"]}
    assert ids == {opp_a.id, opp_b.id}
    candidate = body["candidates"][0]
    assert "against_opponent" in candidate
    assert "against_other_opponents" in candidate
    assert "sufficient_evidence" in candidate
    assert "confidence" in candidate
    assert "adjusted_effect" in candidate
    assert "most recent recorded club" in body["scope_explanation"]


def test_get_opponent_discovery_supports_goals_stat(client, db_session):
    player, _opp_a, _opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/opponent-context-candidates", params={"stat": "goals"})
    assert resp.status_code == 200
    assert resp.json()["stat"] == "goals"


def test_get_opponent_discovery_rejects_unsupported_stat(client, db_session):
    player, _opp_a, _opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/opponent-context-candidates", params={"stat": "tackles"})
    assert resp.status_code == 400


def test_get_opponent_discovery_rejects_malformed_thresholds(client, db_session):
    player, _opp_a, _opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/opponent-context-candidates", params={"thresholds": "abc"})
    assert resp.status_code == 400


def test_get_opponent_discovery_404_for_unknown_player(client, db_session):
    resp = client.get("/api/afl/players/999999/opponent-context-candidates")
    assert resp.status_code == 404


def test_get_opponent_discovery_empty_candidates_when_no_opponent_recorded(client, db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2024)
    db_session.add(season)
    db_session.flush()
    home = Team(sport_id=sport.id, name="North Melbourne", short_name="NM")
    opp = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db_session.add_all([home, opp])
    db_session.flush()
    player = Player(sport_id=sport.id, display_name="Solo Player", source="afltables", source_player_id="players/Z/Z.html")
    db_session.add(player)
    db_session.commit()

    round_ = Round(season_id=season.id, round_number=1)
    db_session.add(round_)
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=opp.id, scheduled_start=BASE, status=MatchStatus.COMPLETED,
    )
    db_session.add(match)
    db_session.flush()
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, opponent_team_id=None,
        source="afltables", recorded_at=BASE, disposals=20, goals=1, time_on_ground_pct=85,
    ))
    db_session.commit()

    resp = client.get(f"/api/afl/players/{player.id}/opponent-context-candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidates"] == []
    assert "No recorded opponent information" in body["explanation"]
