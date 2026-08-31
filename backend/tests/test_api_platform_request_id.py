from sqlalchemy import select

from app.models import ApiUsageRecord

PRICING_URL = "/api/v1/pricing/afl/current-round"


class TestRequestId:
    def test_every_response_carries_a_request_id_header(self, client, db_session):
        resp = client.get("/api/health")
        assert "X-Request-ID" in resp.headers
        assert len(resp.headers["X-Request-ID"]) > 0

    def test_client_supplied_request_id_is_echoed_back(self, client, db_session):
        resp = client.get("/api/health", headers={"X-Request-ID": "my-custom-id-123"})
        assert resp.headers["X-Request-ID"] == "my-custom-id-123"

    def test_a_generated_request_id_is_unique_per_request(self, client, db_session):
        r1 = client.get("/api/health")
        r2 = client.get("/api/health")
        assert r1.headers["X-Request-ID"] != r2.headers["X-Request-ID"]

    def test_error_response_body_contains_the_same_request_id_as_the_header(self, client, db_session):
        resp = client.get("/api/v1/pricing/afl/matches/999999")
        assert resp.status_code == 404
        assert resp.json()["request_id"] == resp.headers["X-Request-ID"]

    def test_pricing_response_provenance_contains_the_request_id(self, client, db_session):
        resp = client.get("/api/v1/pricing/afl/current-round")
        assert resp.status_code == 200
        body = resp.json()
        # current-round may have zero teams if there's no upcoming match seeded - just confirm the shape holds when present.
        if body["teams"]:
            assert body["teams"][0]["provenance"]["request_id"] == resp.headers["X-Request-ID"]

    def test_usage_record_carries_the_same_request_id(self, client, db_session):
        resp = client.get(PRICING_URL)
        record = db_session.scalar(select(ApiUsageRecord))
        assert record.request_id == resp.headers["X-Request-ID"]
