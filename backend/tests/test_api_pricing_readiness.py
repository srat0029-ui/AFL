class TestPricingReadiness:
    def test_empty_db_does_not_crash_and_returns_a_status(self, client, db_session):
        resp = client.get("/api/v1/pricing/readiness")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ready", "degraded", "not_ready")
        assert "checks" in body
        assert "generated_at" in body

    def test_never_returns_not_ready_purely_from_warning_severity_findings(self, client, db_session):
        # With no upcoming matches at all, data_health reports "not_available"
        # freshness items (INFO), never ERROR - readiness must not be "not_ready".
        resp = client.get("/api/v1/pricing/readiness")
        assert resp.json()["status"] != "not_ready"

    def test_readiness_does_not_require_an_api_key(self, client, db_session):
        # Readiness is an operational endpoint, not the priced-data surface -
        # it must stay reachable even outside local dev, with no key.
        from app.config import Settings, get_settings
        from app.main import app

        app.dependency_overrides[get_settings] = lambda: Settings(app_env="production")
        try:
            resp = client.get("/api/v1/pricing/readiness")
        finally:
            del app.dependency_overrides[get_settings]
        assert resp.status_code == 200
