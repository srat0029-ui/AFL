"""API-level tests for the Placed Bets tracker endpoints: create round-trips
the frozen snapshot exactly, list filters by status, delete removes."""

from datetime import datetime, timedelta, timezone

from app.models import Match, MatchStatus, Player, Round, Season, Sport, Team

NOW = datetime.now(timezone.utc)


def _seed(db):
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
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    db.commit()
    return match, home, away, player


def _payload(match, player, **overrides):
    body = dict(
        match_id=match.id, opportunity_type="player", label="Nick Daicos 25+ Disposals", selection="over",
        market_type="player_disposals", bookmaker="SportsBet", odds_taken=1.9,
        model_probability=0.6, model_fair_odds=1.67, confidence_tier="higher_confidence",
        source_mode="high_probability", player_id=player.id, line_type="over_under", threshold=24.5,
        lineup_status="confirmed_selected",
    )
    body.update(overrides)
    return body


def test_create_and_get_placed_bet_round_trips(client, db_session):
    match, home, away, player = _seed(db_session)

    resp = client.post("/api/placed-bets", json=_payload(match, player))
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["model_probability"] == 0.6
    assert body["settled_at"] is None

    get_resp = client.get(f"/api/placed-bets/{body['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["label"] == "Nick Daicos 25+ Disposals"


def test_list_placed_bets_filters_by_status(client, db_session):
    match, home, away, player = _seed(db_session)
    client.post("/api/placed-bets", json=_payload(match, player))

    all_resp = client.get("/api/placed-bets")
    assert len(all_resp.json()) == 1
    won_resp = client.get("/api/placed-bets?status=won")
    assert won_resp.json() == []


def test_delete_placed_bet(client, db_session):
    match, home, away, player = _seed(db_session)
    created = client.post("/api/placed-bets", json=_payload(match, player)).json()

    del_resp = client.delete(f"/api/placed-bets/{created['id']}")
    assert del_resp.status_code == 204
    assert client.get("/api/placed-bets").json() == []
    assert client.delete(f"/api/placed-bets/{created['id']}").status_code == 404


def test_get_missing_bet_is_404(client, db_session):
    assert client.get("/api/placed-bets/999999").status_code == 404


def test_analytics_endpoint_not_swallowed_by_bet_id_route(client, db_session):
    """Regression: /analytics must resolve to the analytics endpoint, not
    be parsed as {bet_id} (which would 422 on a non-integer path segment)."""
    resp = client.get("/api/placed-bets/analytics")
    assert resp.status_code == 200
    assert resp.json()["n_total_settled"] == 0


def test_analytics_reflects_settled_bets(client, db_session):
    match, home, away, player = _seed(db_session)
    created = client.post("/api/placed-bets", json=_payload(match, player)).json()
    # Settle it directly via the same service the live cycle uses - this
    # test only checks the analytics endpoint reads it correctly, not
    # settlement itself (out of scope here).
    from app.models import PlayerMatchStat
    from app.player_modelling.placed_bets import settle_placed_bets

    match.status = MatchStatus.COMPLETED
    db_session.add(PlayerMatchStat(player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", disposals=30, recorded_at=match.scheduled_start))
    db_session.commit()
    settle_placed_bets(db_session)

    resp = client.get("/api/placed-bets/analytics")
    body = resp.json()
    assert body["n_total_settled"] == 1
    assert body["wins"] == 1
    assert body["exploratory"] is True
    assert created["id"] is not None
