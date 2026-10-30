"""Read-only NBA dashboard endpoints: GET /api/nba/outlooks/games and
GET /api/nba/outlooks/games/{id}, over frozen snapshots and V1.5 outlooks."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models.nba import NbaMinutesModelRun, NbaOutlookSnapshot, NbaPlayerAvailabilityReport, NbaRotationPrediction
from app.models.nba.evidence import NbaEvidencePoll
from tests.nba_helpers import add_game_log, seed_game, seed_player

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture()
def no_network(monkeypatch):
    """Any outgoing request (other than the in-process TestClient) fails the test."""
    original = httpx.Client.send

    def send(self, request, *args, **kwargs):
        if request.url.host != "testserver":
            raise AssertionError(f"dashboard endpoint made an outgoing request to {request.url}")
        return original(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)


def _run(db):
    run = NbaMinutesModelRun(run_key="dash", model_name="m", model_version="v", purpose="serving", run_at=NOW, dataset_cutoff=NOW,
                             training_seasons=[], evaluation_seasons=[], features=[], hyperparameters={}, metrics={})
    db.add(run)
    db.flush()
    return run


def _snapshot(db, game, label, offset_min, status, tip=None, reason=None):
    tip = tip or game.scheduled_start
    snap = NbaOutlookSnapshot(
        game_id=game.id, snapshot_label=label, offset_minutes=offset_min, scheduled_tipoff=tip, target_cutoff=tip - timedelta(minutes=offset_min),
        information_cutoff=tip - timedelta(minutes=offset_min), box_score_cutoff=tip - timedelta(minutes=offset_min + 240), generated_at=tip - timedelta(minutes=offset_min),
        status=status, reason=reason, roster_evidence={}, model_versions={"participation": "rotation-v1.0"}, counts={"written": 2},
        evidence_freshness={"injury_feed": {"age_minutes": 12.0, "class": "fresh"}},
    )
    db.add(snap)
    db.flush()
    return snap


def _pred(db, run, snap, game, team, player, p_play, p_rot, minutes, evidence):
    db.add(NbaRotationPrediction(
        game_id=game.id, player_id=player.id, team_id=team.id, generated_at=snap.generated_at, information_cutoff=snap.information_cutoff,
        snapshot_id=snap.id, minutes_model_run_id=run.id, participation_model_run_id=run.id, rotation_model_run_id=run.id,
        p_play=p_play, p_rotation=p_rot, expected_minutes_if_plays=minutes, quantiles={"p10": 20, "p90": 38} if minutes else None,
        tier="likely_rotation" if p_rot >= 0.7 else "uncertain", team_source="rostered", roster_listed=True, history_games=30, availability_evidence=evidence,
    ))


def _seed(db, tip):
    game, home, away = seed_game(db, tipoff=tip, source_game_id="dash")
    game.season_type = "regular"
    star = seed_player(db, home, "Star Guard", "s1")
    bench = seed_player(db, home, "Bench Wing", "s2")
    visitor = seed_player(db, away, "Visiting Center", "s3")
    run = _run(db)
    t24 = _snapshot(db, game, "T-24h", 1440, "produced")
    t4 = _snapshot(db, game, "T-4h", 240, "stale", reason="injury feed 120 min old")
    _snapshot(db, game, "T-1h", 60, "produced", tip=tip - timedelta(hours=2))  # frozen for an earlier tip-off: not shown
    for snap in (t24, t4):
        _pred(db, run, snap, game, home, star, 0.95, 0.93, 34.1, {"state": "never_listed"})
        _pred(db, run, snap, game, home, bench, 0.55, 0.30, 14.0, {"state": "listed", "status": None, "source_status": "Day-To-Day"})
        _pred(db, run, snap, game, away, visitor, 0.90, 0.85, 28.0, {"state": "never_listed"})
    poll = NbaEvidencePoll(kind="availability", source="test", observed_at=NOW - timedelta(minutes=5), items_seen=1, observations_added=1)
    db.add(poll)
    db.flush()
    db.add(NbaPlayerAvailabilityReport(poll_id=poll.id, player_id=bench.id, team_id=home.id, is_listed=True, source_status="Out", status="out",
                                       source="test", observed_at=NOW - timedelta(minutes=5)))
    db.commit()
    return game, home, away, star, bench, visitor


def test_games_list_shows_window_states(client, db_session, no_network):
    game, home, away, *_ = _seed(db_session, NOW + timedelta(hours=2))
    body = client.get("/api/nba/outlooks/games").json()
    (g,) = body["games"]
    assert (g["game_id"], g["home"]["abbreviation"], g["away"]["abbreviation"]) == (game.id, home.abbreviation, away.abbreviation)
    states = {w["label"]: w["state"] for w in g["windows"]}
    # T-1h is still ahead (its only snapshot belongs to an earlier tip-off), T-30m too.
    assert states == {"T-24h": "produced", "T-4h": "stale", "T-1h": "upcoming", "T-30m": "upcoming"}
    assert g["latest_produced"] == "T-24h"
    assert "live_evidence" in body and "healthy" in body["live_evidence"]


def test_games_outside_the_window_are_not_listed(client, db_session, no_network):
    _seed(db_session, NOW + timedelta(days=5))
    assert client.get("/api/nba/outlooks/games").json()["games"] == []
    assert len(client.get("/api/nba/outlooks/games", params={"hours_ahead": 168}).json()["games"]) == 1


def test_passed_window_without_a_snapshot_is_none(client, db_session, no_network):
    _seed(db_session, NOW - timedelta(minutes=10))  # T-1h for this tip-off has passed; the only T-1h is for an old tip-off
    (g,) = client.get("/api/nba/outlooks/games").json()["games"]
    assert {w["label"]: w["state"] for w in g["windows"]}["T-1h"] == "none"


def test_game_detail_players_windows_and_current_evidence(client, db_session, no_network):
    game, home, away, star, bench, visitor = _seed(db_session, NOW + timedelta(hours=2))
    body = client.get(f"/api/nba/outlooks/games/{game.id}").json()
    assert body["labels"] == ["T-24h", "T-4h", "T-1h", "T-30m"]
    windows = {w["label"]: w for w in body["windows"]}
    assert windows["T-4h"]["reason"] == "injury feed 120 min old"
    assert windows["T-24h"]["model_versions"] == {"participation": "rotation-v1.0"}
    assert windows["T-24h"]["evidence_freshness"]["injury_feed"]["class"] == "fresh"
    assert windows["T-1h"]["snapshot_id"] is None
    home_players = body["home"]["players"]
    assert [p["name"] for p in home_players] == ["Star Guard", "Bench Wing"]  # most likely rotation first
    star_row, bench_row = home_players
    assert set(star_row["outlooks"]) == {"T-24h", "T-4h"} and star_row["latest_label"] == "T-4h"
    o = star_row["outlooks"]["T-24h"]
    assert (o["p_play"], o["p_rotation"], o["expected_minutes_if_plays"], o["quantiles"]) == (0.95, 0.93, 34.1, {"p10": 20, "p90": 38})
    # What the snapshot saw vs what the feed says now.
    assert bench_row["outlooks"]["T-4h"]["availability_evidence"]["source_status"] == "Day-To-Day"
    assert bench_row["current_availability"]["state"] == "listed" and bench_row["current_availability"]["status"] == "out"
    assert star_row["current_availability"]["state"] == "never_listed"
    assert star_row["result"] is None  # not final yet
    assert [p["name"] for p in body["away"]["players"]] == ["Visiting Center"]


def test_final_game_shows_what_happened(client, db_session, no_network):
    game, home, away, star, bench, visitor = _seed(db_session, NOW - timedelta(hours=8))
    game.status, game.box_score_state = "final", "ingested"
    add_game_log(db_session, game=game, player=star, team=home, opponent=away, minutes=36)
    db_session.commit()
    rows = {p["name"]: p for p in client.get(f"/api/nba/outlooks/games/{game.id}").json()["home"]["players"]}
    assert rows["Star Guard"]["result"] == {"dressed": True, "played": True, "minutes": 36}
    assert rows["Bench Wing"]["result"] == {"dressed": False, "played": False, "minutes": None}


def test_unknown_game_is_404(client, no_network):
    assert client.get("/api/nba/outlooks/games/999999").status_code == 404


def test_game_with_no_snapshots_has_no_players(client, db_session, no_network):
    game, *_ = seed_game(db_session, tipoff=NOW + timedelta(hours=30), source_game_id="bare")
    game.season_type = "regular"
    db_session.commit()
    body = client.get(f"/api/nba/outlooks/games/{game.id}").json()
    assert body["home"]["players"] == [] and body["away"]["players"] == []
    assert [w["state"] for w in body["windows"]] == ["upcoming"] * 4
