"""API-level tests for the Current Context + Team News Intelligence
stage's endpoints (Section 18) — provenance round-trip, confirmed-out
gating via apply_to_lineup, manual-override coexistence, and the
match-context panel/dashboard shapes."""

from datetime import datetime, timedelta, timezone

from app.models import ExpectedLineup, Match, MatchStatus, Player, Round, Season, Sport, Team

NOW = datetime.now(timezone.utc)


def _seed(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    db.add(round_)
    db.flush()
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Test Player", current_team_id=home.id, source="afltables", source_player_id="players/T/Test_Player.html")
    db.add(player)
    db.commit()
    return match, home, away, player


def test_create_context_item_round_trips_provenance(client, db_session):
    match, home, away, player = _seed(db_session)
    resp = client.post(
        f"/api/afl/matches/{match.id}/context",
        json={
            "context_type": "injury",
            "source": "Club injury update",
            "summary": "Rolled an ankle at training",
            "confidence": "official",
            "team_id": home.id,
            "player_id": player.id,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["item"]["source"] == "Club injury update"
    assert body["item"]["confidence"] == "official"
    assert body["item"]["context_type_label"] == "Injury"
    assert body["lineup_updated"] is False  # "injury" has no lineup-status mapping


def test_apply_to_lineup_confirms_player_out(client, db_session):
    match, home, away, player = _seed(db_session)
    resp = client.post(
        f"/api/afl/matches/{match.id}/context",
        json={
            "context_type": "confirmed_out", "source": "Official team announcement", "summary": "Out with injury",
            "confidence": "official", "team_id": home.id, "player_id": player.id, "apply_to_lineup": True,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["lineup_updated"] is True

    lineup = db_session.query(ExpectedLineup).filter(ExpectedLineup.match_id == match.id, ExpectedLineup.player_id == player.id).one()
    assert lineup.selection_status == "confirmed_out"
    assert lineup.status == "expected_out"


def test_apply_to_lineup_respects_existing_manual_override(client, db_session):
    match, home, away, player = _seed(db_session)
    lineup = ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=home.id, status="expected_in", selection_status="confirmed_selected",
        is_confirmed=True, recorded_at=NOW, source="manual", is_manual_override=True,
    )
    db_session.add(lineup)
    db_session.commit()

    resp = client.post(
        f"/api/afl/matches/{match.id}/context",
        json={
            "context_type": "confirmed_out", "source": "Manual", "summary": "Reported out (unverified)",
            "confidence": "unverified", "team_id": home.id, "player_id": player.id, "apply_to_lineup": True,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["lineup_updated"] is False
    assert "manual override" in body["lineup_apply_note"].lower()

    db_session.refresh(lineup)
    assert lineup.selection_status == "confirmed_selected"  # untouched


def test_context_history_and_current_endpoints(client, db_session):
    match, home, away, player = _seed(db_session)
    client.post(
        f"/api/afl/matches/{match.id}/context",
        json={"context_type": "limited_game_time_concern", "source": "Manual", "summary": "Managed minutes", "confidence": "unverified", "player_id": player.id, "team_id": home.id},
    )
    client.post(
        f"/api/afl/matches/{match.id}/context",
        json={"context_type": "confirmed_out", "source": "Official team announcement", "summary": "Confirmed out", "confidence": "official", "player_id": player.id, "team_id": home.id},
    )

    history = client.get(f"/api/afl/matches/{match.id}/context").json()
    assert len(history) == 2

    current = client.get(f"/api/afl/matches/{match.id}/context/current").json()
    assert len(current) == 1
    assert current[0]["context_type"] == "confirmed_out"


def test_context_panel_includes_last_updated(client, db_session):
    match, home, away, player = _seed(db_session)
    client.post(
        f"/api/afl/matches/{match.id}/context",
        json={"context_type": "confirmed_out", "source": "Manual", "summary": "Out", "confidence": "unverified", "player_id": player.id, "team_id": home.id},
    )
    resp = client.get(f"/api/afl/matches/{match.id}/context-panel")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["current_context"]) == 1
    assert body["last_updated"] is not None
    assert body["weather"] is None


def test_context_panel_404_for_missing_match(client, db_session):
    resp = client.get("/api/afl/matches/999999/context-panel")
    assert resp.status_code == 404


def test_context_dashboard_empty_when_no_upcoming_matches(client, db_session):
    resp = client.get("/api/afl/context-dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["matches"] == []


def test_create_context_item_unknown_player_404s(client, db_session):
    match, home, away, player = _seed(db_session)
    resp = client.post(
        f"/api/afl/matches/{match.id}/context",
        json={"context_type": "confirmed_out", "source": "Manual", "summary": "Out", "confidence": "unverified", "player_id": 999999},
    )
    assert resp.status_code == 404
