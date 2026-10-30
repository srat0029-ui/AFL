"""The read-only NBA evidence audit (.github/workflows/nba-evidence-audit.yml,
backend/app/nba/audit_sql/): its SQL only reads, returns aggregates, and
computes what it claims on real PostgreSQL."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.nba import NbaGameLineupObservation, NbaMinutesModelRun, NbaOutlookSnapshot, NbaPlayerAvailabilityReport, NbaRotationPrediction
from app.models.nba.evidence import NbaEvidencePoll
from tests.nba_helpers import add_game_log, seed_game, seed_player

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "nba-evidence-audit.yml"
SQL_DIR = REPO / "backend" / "app" / "nba" / "audit_sql"
TIP = datetime(2026, 10, 30, 2, 0, tzinfo=timezone.utc)


def test_audit_sql_is_read_only_and_aggregate():
    files = sorted(SQL_DIR.glob("*.sql"))
    assert [f.stem for f in files] == ["1_volume", "2_feed_vocabulary", "3_lineup_timing", "4_status_outcomes", "5_coverage"]
    for path in files:
        sql = re.sub(r"--.*", "", path.read_text(encoding="utf-8")).upper()
        assert sql.strip().startswith(("WITH", "SELECT")), path.name
        for forbidden in ("INSERT", "UPDATE ", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "COPY", "SET "):
            assert forbidden not in sql, (path.name, forbidden)
        # aggregates only: never player names or ESPN's free-text comments
        for column in ("DISPLAY_NAME", "SHORT_COMMENT", "LONG_COMMENT", "INJURY_DETAIL", "PAYLOAD"):
            assert column not in sql, (path.name, column)


def test_audit_workflow_safeguards():
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in wf and "schedule:" not in wf
    assert "contents: read" in wf and "write" not in wf.split("permissions:")[1].split("jobs:")[0]
    assert "READONLY='-c default_transaction_read_only=on'" in wf and 'PGOPTIONS="$READONLY"' in wf
    assert "--single-transaction" in wf and "ON_ERROR_STOP=1" in wf
    assert "group: nba-evidence-audit" in wf
    for forbidden in ("vars.NBA_LIVE_CYCLE_IMAGE", "app.nba.cli", "alembic", "gh issue", "gh variable", "pg_restore", "pg_dump", "watchdog."):
        assert forbidden not in wf, forbidden


# --- on real PostgreSQL ----------------------------------------------------------


def _pg(db):
    if db.get_bind().dialect.name != "postgresql":
        pytest.skip("the audit SQL runs on the production PostgreSQL only")


def _run(db, name: str) -> list[dict]:
    result = db.connection().exec_driver_sql((SQL_DIR / f"{name}.sql").read_text(encoding="utf-8"))
    return [dict(row._mapping) for row in result]


def _seed(db):
    game, home, away = seed_game(db, tipoff=TIP, source_game_id="audit")
    game.status, game.box_score_state, game.season_type = "final", "ingested", "regular"
    out, dtd, unlisted_bench, newcomer = (seed_player(db, home, f"P{i}", f"a{i}") for i in range(4))
    add_game_log(db, game=game, player=dtd, team=home, opponent=away, minutes=20)  # questionable-type, played
    add_game_log(db, game=game, player=unlisted_bench, team=home, opponent=away, minutes=None, did_not_play=True)  # dressed, DNP
    add_game_log(db, game=game, player=newcomer, team=home, opponent=away, minutes=12)  # played, no outlook
    # `out` was listed Out and never dressed: no box-score row.

    poll = NbaEvidencePoll(kind="availability", source="test", observed_at=TIP - timedelta(hours=5), items_seen=2, observations_added=2)
    db.add(poll)
    db.flush()
    for player, raw, canonical in ((out, "Out", "out"), (dtd, "Day-To-Day", None)):
        db.add(NbaPlayerAvailabilityReport(
            poll_id=poll.id, player_id=player.id, team_id=home.id, is_listed=True, source_status=raw, status=canonical,
            fantasy_status="OUT" if canonical else "GTD", source="test", source_team_id="1", source_athlete_team_id="1", observed_at=TIP - timedelta(hours=5),
        ))
    lineup_poll = NbaEvidencePoll(kind="lineups", source="test", observed_at=TIP - timedelta(minutes=20), items_seen=1, observations_added=1)
    db.add(lineup_poll)
    db.flush()
    db.add(NbaGameLineupObservation(
        poll_id=lineup_poll.id, game_id=game.id, team_id=home.id, source="test", observed_at=TIP - timedelta(minutes=20), tipoff_at_observation=TIP,
        game_status_at_observation="scheduled", lineup_available=True, has_starter_field=True, starters_flagged=5, players_listed=13, payload={}, content_hash="x",
    ))

    run = NbaMinutesModelRun(
        run_key="audit-test", model_name="m", model_version="v", purpose="serving", run_at=TIP, dataset_cutoff=TIP,
        training_seasons=[], evaluation_seasons=[], features=[], hyperparameters={}, metrics={},
    )
    db.add(run)
    db.flush()
    snap = NbaOutlookSnapshot(
        game_id=game.id, snapshot_label="T-1h", offset_minutes=60, scheduled_tipoff=TIP, target_cutoff=TIP - timedelta(hours=1),
        information_cutoff=TIP - timedelta(hours=1), box_score_cutoff=TIP - timedelta(hours=5), generated_at=TIP - timedelta(hours=1),
        status="produced", roster_evidence={}, model_versions={}, counts={},
    )
    db.add(snap)
    db.flush()
    evidence = {
        out.id: {"state": "listed", "status": "out", "source_status": "Out"},
        dtd.id: {"state": "listed", "status": None, "source_status": "Day-To-Day"},
        unlisted_bench.id: {"state": "never_listed"},
    }
    for player_id, ev in evidence.items():
        db.add(NbaRotationPrediction(
            game_id=game.id, player_id=player_id, team_id=home.id, generated_at=TIP - timedelta(hours=1), information_cutoff=TIP - timedelta(hours=1),
            snapshot_id=snap.id, minutes_model_run_id=run.id, participation_model_run_id=run.id, rotation_model_run_id=run.id,
            p_play=0.8, p_rotation=0.6, expected_minutes_if_plays=20.0, tier="likely_active", team_source="rostered", roster_listed=True,
            history_games=10, availability_evidence=ev,
        ))
    db.flush()
    return game


def test_audit_sql_on_postgresql(db_session):
    _pg(db_session)
    _seed(db_session)

    volume = {r["item"]: r["value"] for r in _run(db_session, "1_volume")}
    assert volume["final competitive 2026-27 games with a full box score"] == "1"
    assert volume["produced T-1h snapshots of those games"] == "1"
    assert volume["pregame lineup observations (competitive 2026-27 games)"] == "1"

    vocab = {(r["source_status"], r["status"]): r["observations"] for r in _run(db_session, "2_feed_vocabulary")}
    assert vocab == {("Out", "out"): 1, ("Day-To-Day", ""): 1}

    lineup = _run(db_session, "3_lineup_timing")
    assert [(r["bucket"], r["with_starters"], r["mean_players_listed"]) for r in lineup] == [("4 15-30 min before", 1, 13)]

    outcomes = {(r["state"], r["status"]): r for r in _run(db_session, "4_status_outcomes")}
    assert set(outcomes) == {("listed", "out"), ("listed", "day-to-day"), ("never_listed", "")}
    assert (outcomes[("listed", "out")]["dressed_rate"], outcomes[("listed", "out")]["played_rate"]) == (0, 0)
    assert (outcomes[("listed", "day-to-day")]["played_rate"], outcomes[("listed", "day-to-day")]["mean_minutes_if_played"]) == (1, 20)
    assert (outcomes[("never_listed", "")]["dressed_rate"], outcomes[("never_listed", "")]["played_rate"]) == (1, 0)

    (coverage,) = _run(db_session, "5_coverage")
    assert (coverage["played"], coverage["played_without_outlook"]) == (2, 1)
    assert float(coverage["minutes_share_without_outlook"]) == pytest.approx(12 / 32, abs=1e-4)
