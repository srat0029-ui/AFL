def test_health_liveness(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_db_round_trip(client):
    """Proves the API -> database path works end to end (empty DB case)."""
    response = client.get("/api/health/db")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["sport_rows"] == 0


def test_health_db_reflects_data(client, db_session):
    from app.models import Sport

    db_session.add(Sport(code="AFL", name="Australian Football League"))
    db_session.commit()

    response = client.get("/api/health/db")
    assert response.json()["sport_rows"] == 1


def test_health_db_degrades_to_503_on_database_error(client, db_session, monkeypatch):
    """A DB failure must be a clear 503 'not ready', not a generic 500 -
    so a platform's readiness probe can tell 'database is down' apart from
    'the application has a bug'."""
    from sqlalchemy.exc import OperationalError

    def _boom(*args, **kwargs):
        raise OperationalError("statement", {}, Exception("connection refused"))

    monkeypatch.setattr(db_session, "scalar", _boom)
    response = client.get("/api/health/db")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unreachable"}


def test_release_endpoint_exposes_safe_provenance_fields(client):
    response = client.get("/api/release")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"git_sha", "build_time", "app_version", "app_env"}
    assert body["git_sha"]  # never empty - "unknown" at worst
    assert body["app_env"] == "local"
