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
