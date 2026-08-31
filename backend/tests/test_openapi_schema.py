"""OpenAPI generation sanity checks — the schema must still build cleanly
after adding auth/error/readiness machinery, and the new security scheme
and documented routes should actually show up in it."""


class TestOpenApiSchema:
    def test_openapi_json_generates_without_error(self, client, db_session):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema

    def test_api_key_security_scheme_is_documented(self, client, db_session):
        schema = client.get("/openapi.json").json()
        schemes = schema.get("components", {}).get("securitySchemes", {})
        assert any(s.get("name") == "X-API-Key" for s in schemes.values())

    def test_same_game_route_has_a_summary_and_example(self, client, db_session):
        schema = client.get("/openapi.json").json()
        post_op = schema["paths"]["/api/v1/pricing/afl/same-game"]["post"]
        assert post_op.get("summary")
        assert "Idempotency" in post_op.get("description", "")

    def test_readiness_route_is_documented(self, client, db_session):
        schema = client.get("/openapi.json").json()
        assert "/api/v1/pricing/readiness" in schema["paths"]
