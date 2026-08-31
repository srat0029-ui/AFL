"""Item 15 hardening: SGM leg count and n_simulations are both bounded
server-side regardless of what an external client requests."""


def _sgm_body(n_legs=2, n_simulations=100_000):
    legs = [{"leg_type": "h2h", "team_id": 1}]
    legs += [{"leg_type": "disposals", "player_id": i, "threshold": 20.5} for i in range(n_legs - 1)]
    return {"match_id": 1, "legs": legs, "n_simulations": n_simulations}


class TestSgmInputLimits:
    def test_n_simulations_above_the_ceiling_is_rejected(self, client, db_session):
        resp = client.post("/api/v1/pricing/afl/same-game", json=_sgm_body(n_simulations=50_000_000))
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "VALIDATION_ERROR"

    def test_n_simulations_below_the_floor_is_rejected(self, client, db_session):
        resp = client.post("/api/v1/pricing/afl/same-game", json=_sgm_body(n_simulations=10))
        assert resp.status_code == 422

    def test_n_simulations_at_the_ceiling_passes_schema_validation(self, client, db_session):
        # 200_000 is a valid n_simulations; this may still 400/404 deeper in
        # the pricing engine (no real match/projection seeded) - the point
        # is it must NOT be rejected at the schema/limit-validation layer.
        resp = client.post("/api/v1/pricing/afl/same-game", json=_sgm_body(n_simulations=200_000))
        assert resp.status_code != 422

    def test_too_many_legs_is_rejected(self, client, db_session):
        resp = client.post("/api/v1/pricing/afl/same-game", json=_sgm_body(n_legs=9))
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "VALIDATION_ERROR"

    def test_max_legs_passes_schema_validation(self, client, db_session):
        resp = client.post("/api/v1/pricing/afl/same-game", json=_sgm_body(n_legs=8))
        assert resp.status_code != 422

    def test_fewer_than_two_legs_is_rejected(self, client, db_session):
        resp = client.post("/api/v1/pricing/afl/same-game", json={"match_id": 1, "legs": [{"leg_type": "h2h", "team_id": 1}]})
        assert resp.status_code == 422
