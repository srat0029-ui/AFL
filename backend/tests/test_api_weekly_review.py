"""API-level tests for the Weekly Review stage's endpoints — schema
round-trips over an empty/synthetic DB (the full real-data walkthrough is
covered separately by this stage's real-verification report)."""


def test_weekly_review_page_endpoint_returns_valid_schema_on_empty_db(client, db_session):
    resp = client.get("/api/afl/weekly-review")
    assert resp.status_code == 200
    body = resp.json()
    assert body["final_shortlist"] == []
    assert body["strongest_player_opportunities"] == []
    assert body["strongest_team_opportunities"] == []
    assert "model_vs_market_disagreements_count" in body
    assert "any_confirmed_player_lineups" in body


def test_create_and_list_snapshot_on_empty_db(client, db_session):
    resp = client.post("/api/afl/weekly-review/shortlist-snapshots", json={"label": "empty test"})
    assert resp.status_code == 201
    snap = resp.json()
    assert snap["n_items"] == 0
    assert snap["label"] == "empty test"

    list_resp = client.get("/api/afl/weekly-review/shortlist-snapshots")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


def test_get_missing_snapshot_404(client, db_session):
    resp = client.get("/api/afl/weekly-review/shortlist-snapshots/999999")
    assert resp.status_code == 404


def test_settle_missing_snapshot_404(client, db_session):
    resp = client.post("/api/afl/weekly-review/shortlist-snapshots/999999/settle")
    assert resp.status_code == 404


def test_round_summary_missing_snapshot_404(client, db_session):
    resp = client.get("/api/afl/weekly-review/shortlist-snapshots/999999/round-summary")
    assert resp.status_code == 404


def test_round_summary_on_empty_snapshot(client, db_session):
    create_resp = client.post("/api/afl/weekly-review/shortlist-snapshots", json={})
    snap_id = create_resp.json()["id"]
    resp = client.get(f"/api/afl/weekly-review/shortlist-snapshots/{snap_id}/round-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_items"] == 0
    assert body["small_sample_warning"] is True
