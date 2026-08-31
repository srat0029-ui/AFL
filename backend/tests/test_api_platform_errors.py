"""The unified error contract - {error_code, message, request_id, details}
- applied both to purpose-raised ApiError subclasses and, for free, to the
~40 pre-existing routes that just raise a plain HTTPException."""


class TestErrorContractShape:
    def test_not_found_error_shape(self, client, db_session):
        resp = client.get("/api/v1/pricing/afl/matches/999999")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error_code"] == "NOT_FOUND"
        assert body["message"]
        assert "request_id" in body
        assert body["details"] is None

    def test_model_unavailable_maps_to_service_unavailable(self, client, db_session):
        # No elo/poisson ModelRun seeded -> build_model_context raises
        # ModelsUnavailableError -> route raises HTTPException(503, ...).
        from datetime import datetime, timezone

        from app.models import Match, MatchStatus, Round, Season, Sport, Team

        sport = Sport(code="AFL", name="Australian Football League")
        db_session.add(sport)
        db_session.flush()
        season = Season(sport_id=sport.id, year=2026)
        db_session.add(season)
        db_session.flush()
        round_ = Round(season_id=season.id, round_number=1)
        home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
        away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
        db_session.add_all([round_, home, away])
        db_session.flush()
        match = Match(sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id, scheduled_start=datetime.now(timezone.utc), status=MatchStatus.SCHEDULED)
        db_session.add(match)
        db_session.commit()

        resp = client.get(f"/api/v1/pricing/afl/matches/{match.id}")
        assert resp.status_code == 503
        assert resp.json()["error_code"] == "SERVICE_UNAVAILABLE"

    def test_validation_error_from_malformed_body_returns_consistent_shape(self, client, db_session):
        resp = client.post("/api/v1/pricing/afl/same-game", json={"match_id": "not-an-int", "legs": []})
        assert resp.status_code == 422
        body = resp.json()
        assert body["error_code"] == "VALIDATION_ERROR"
        assert "request_id" in body
        assert "errors" in body["details"]

    def test_error_response_never_leaks_a_stack_trace(self, client, db_session):
        resp = client.get("/api/v1/pricing/afl/matches/999999")
        text = resp.text
        assert "Traceback" not in text
        assert ".py\"" not in text  # no file path fragments from a traceback

    def test_unsupported_market_returns_validation_error_code(self, client, db_session):
        resp = client.get("/api/v1/market-intelligence/afl/players/1/not_a_real_market", params={"match_id": 1, "threshold": 20.5})
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "VALIDATION_ERROR"
