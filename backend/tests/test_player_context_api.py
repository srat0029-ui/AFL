"""API-level tests for the player context analysis endpoint, exercised
through the real FastAPI TestClient (see tests/conftest.py).
"""

from datetime import datetime, timedelta, timezone

from app.models import Match, MatchStatus, Player, PlayerMatchStat, PlayerTagAnnotation, Round, Season, Sport, Team
from app.models.match_context import ContextConfidence

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


def test_get_player_context_returns_full_shape(client, db_session):
    player, teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/context/{teammate.id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["player_id"] == player.id
    assert body["player_name"] == "Harry Sheezel"
    assert body["teammate_id"] == teammate.id
    assert body["teammate_name"] == "Luke Davies-Uniacke"
    assert body["stat"] == "disposals"
    assert body["with_teammate"]["games"] == 3
    assert body["without_teammate"]["games"] == 3
    assert "15+" in body["with_teammate"]["milestone_rates"] or "20+" in body["with_teammate"]["milestone_rates"]
    assert len(body["evidence"]) == 6
    assert body["role_analysis_available"] is False
    assert "role" in body["role_analysis_explanation"].lower()
    assert body["tag_watch"]["status"] == "insufficient_verified_data"
    assert set(body["confounders"].keys()) == {"recent_form", "opponent_strength", "venue", "role"}


def test_get_player_context_with_goals_stat_and_custom_thresholds(client, db_session):
    player, teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/context/{teammate.id}", params={"stat": "goals", "thresholds": "1,2"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stat"] == "goals"
    assert set(body["with_teammate"]["milestone_rates"].keys()) == {"1+", "2+"}


def test_get_player_context_rejects_same_player_and_teammate(client, db_session):
    player, _teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/context/{player.id}")
    assert resp.status_code == 400


def test_get_player_context_rejects_unsupported_stat(client, db_session):
    player, teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/context/{teammate.id}", params={"stat": "tackles"})
    assert resp.status_code == 400


def test_get_player_context_rejects_malformed_thresholds(client, db_session):
    player, teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/context/{teammate.id}", params={"thresholds": "abc"})
    assert resp.status_code == 400


def test_get_player_context_404_for_unknown_player(client, db_session):
    _player, teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/999999/context/{teammate.id}")
    assert resp.status_code == 404


def test_get_player_context_404_for_unknown_teammate(client, db_session):
    player, _teammate = _seed_scenario(db_session)
    resp = client.get(f"/api/afl/players/{player.id}/context/999999")
    assert resp.status_code == 404


def test_tag_watch_reports_available_once_enough_verified_annotations_exist(client, db_session):
    player, teammate = _seed_scenario(db_session)
    match_ids = [row.match_id for row in db_session.query(PlayerMatchStat).filter_by(player_id=player.id).all()]
    for i, match_id in enumerate(match_ids[:5]):
        db_session.add(PlayerTagAnnotation(
            match_id=match_id, tagged_player_id=player.id, confidence=ContextConfidence.REPUTABLE_SOURCE.value,
            source="manual_review", recorded_at=BASE + timedelta(days=i),
        ))
    db_session.commit()

    resp = client.get(f"/api/afl/players/{player.id}/context/{teammate.id}")
    assert resp.status_code == 200
    tag_watch = resp.json()["tag_watch"]
    assert tag_watch["status"] == "available"
    assert tag_watch["verified_annotation_count"] == 5
    assert tag_watch["games_played"] == 6
    assert tag_watch["tag_rate"] == 5 / 6
