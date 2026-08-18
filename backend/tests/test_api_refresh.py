"""Tests for the single frontend-triggered refresh endpoint (product-polish
stage): POST-only wiring, response shape reuses LiveCycleRunRead, and a
concurrent request is rejected (409) rather than allowed to double-run the
live cycle and double-spend odds quota."""

from app.api.routes import refresh as refresh_route


def test_get_is_not_allowed(client):
    """Refreshing must never be triggerable by a page load - only an
    explicit POST from the frontend's button click."""
    response = client.get("/api/afl/refresh")
    assert response.status_code == 405


def test_post_triggers_a_live_cycle_run_and_returns_its_summary(client):
    response = client.post("/api/afl/refresh")
    assert response.status_code == 200
    body = response.json()
    assert "id" in body
    assert "overall_status" in body
    assert "steps" in body
    assert "team_odds_quotes_added" in body
    assert "weather_snapshots_added" in body


def test_concurrent_refresh_is_rejected_with_409(client):
    assert refresh_route._refresh_lock.acquire(blocking=False) is True
    try:
        response = client.post("/api/afl/refresh")
        assert response.status_code == 409
    finally:
        refresh_route._refresh_lock.release()
