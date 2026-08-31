"""API-level tests for /api/v1/trading-monitor/* - mostly empty-DB smoke
tests (the composition logic itself is covered by
tests/test_trading_monitor_overview.py, tests/test_data_health.py,
tests/test_sgm_monitor.py, tests/test_model_movement.py)."""


def test_overview_empty_db_returns_sane_empty_structure(client, db_session):
    resp = client.get("/api/v1/trading-monitor/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["n_upcoming_matches"] == 0
    assert body["needs_attention"] == []
    assert body["model_movers"] == []
    assert body["sgm"]["n_recent_snapshots"] == 0
    assert body["data_health"]["backlog"]["prop_observations_unsettled"] == 0


def test_data_health_endpoint(client, db_session):
    resp = client.get("/api/v1/trading-monitor/data-health")
    assert resp.status_code == 200
    body = resp.json()
    assert "freshness" in body
    assert "backlog" in body
    assert "live_cycle" in body


def test_sgm_endpoint(client, db_session):
    resp = client.get("/api/v1/trading-monitor/sgm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_recent_snapshots"] == 0
    assert body["coefficient_provenance"] == []


def test_overview_limit_is_clamped(client, db_session):
    resp = client.get("/api/v1/trading-monitor/overview?limit=9999")
    assert resp.status_code == 200  # clamped server-side, never errors
