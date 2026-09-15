"""API-level tests for the opponent context analysis endpoint, exercised
through the real FastAPI TestClient (see tests/conftest.py).
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


def test_get_opponent_context_returns_full_shape(client, db_session):
    player, opp_a, opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/opponent-context/{opp_a.id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["player_id"] == player.id
    assert body["player_name"] == "Harry Sheezel"
    assert body["opponent_team_id"] == opp_a.id
    assert body["opponent_team_name"] == "Carlton"
    assert body["stat"] == "disposals"
    assert body["against_opponent"]["games"] == 3
    assert body["against_other_opponents"]["games"] == 3
    assert "15+" in body["against_opponent"]["milestone_rates"] or "20+" in body["against_opponent"]["milestone_rates"]
    assert len(body["evidence"]) == 6
    assert all("is_home" in row and "is_selected_opponent" in row for row in body["evidence"])
    assert body["role_analysis_available"] is False
    assert set(body["confounders"].keys()) == {"recent_form", "venue", "role", "lineup_and_era_context"}
    assert "most recent recorded club" in body["scope_explanation"]


def test_get_opponent_context_with_goals_stat_and_custom_thresholds(client, db_session):
    player, opp_a, opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/opponent-context/{opp_a.id}", params={"stat": "goals", "thresholds": "1,2"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stat"] == "goals"
    assert set(body["against_opponent"]["milestone_rates"].keys()) == {"1+", "2+"}


def test_get_opponent_context_rejects_unsupported_stat(client, db_session):
    player, opp_a, opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/opponent-context/{opp_a.id}", params={"stat": "tackles"})
    assert resp.status_code == 400


def test_get_opponent_context_rejects_malformed_thresholds(client, db_session):
    player, opp_a, opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/opponent-context/{opp_a.id}", params={"thresholds": "abc"})
    assert resp.status_code == 400


def test_get_opponent_context_404_for_unknown_player(client, db_session):
    _player, opp_a, _opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/999999/opponent-context/{opp_a.id}")
    assert resp.status_code == 404


def test_get_opponent_context_404_for_unknown_opponent(client, db_session):
    player, _opp_a, _opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/opponent-context/999999")
    assert resp.status_code == 404


def test_get_opponent_context_adjusted_effect_field_names_are_opponent_specific(client, db_session):
    player, opp_a, opp_b = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/opponent-context/{opp_a.id}")
    assert resp.status_code == 200
    adjusted = resp.json()["adjusted_effect"]
    assert "games_with_baseline_against_opponent" in adjusted
    assert "games_with_baseline_other_opponents" in adjusted
