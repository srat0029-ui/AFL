"""NBA production watchdog: the evaluator (app/nba/watchdog.py), its two
read-only SQL checks against real PostgreSQL, and the workflow's safeguards."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.nba import NbaGame, NbaOutlookSnapshot, NbaTeam
from app.nba import watchdog
from app.nba.watchdog import Report, check_backups, check_box_scores, check_health, check_snapshots, render_summary

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "nba-watchdog.yml"
SQL_DIR = REPO / "backend" / "app" / "nba" / "watchdog_sql"
NOW = datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)

HEALTH_OK = """Unable to find image locally
NBA live cycle run 700: ok
evidence health: OK (checked 2026-10-30T03:00:00+00:00)
  latest run: 2026-10-30T02:54:10+00:00 (ok); latest fully successful run: 2026-10-30T02:54:10+00:00
  Injury / availability feed   last success 6 min ago       interval    30 min  ok
"""
HEALTH_STALE = """evidence health: STALE (checked 2026-10-30T03:00:00+00:00)
  latest run: 2026-10-30T01:00:00+00:00 (ok); latest fully successful run: 2026-10-30T01:00:00+00:00
  Injury / availability feed   last success 120 min ago     interval    30 min  STALE
  problem: Injury / availability feed: last successful poll 120 minutes ago (stale after 90)
"""
HEALTH_DB_ASLEEP = """Traceback (most recent call last):
  File "/app/app/nba/cli.py", line 234, in <module>
sqlalchemy.exc.OperationalError: (psycopg.OperationalError) connection failed: FATAL:  This database is held asleep
"""


def _row(label, due, status, game="10937", tip="2026-10-30T02:00Z", matchup="MEM@POR"):
    return {"game_id": game, "tipoff": tip, "matchup": matchup, "label": label, "due": "t" if due else "f", "status": status}


# --- evaluator ---------------------------------------------------------------


def test_healthy_evidence_is_not_a_problem():
    report = Report()
    check_health(HEALTH_OK, 0, report)
    assert report.problems == [] and report.health_lines[0].startswith("evidence health: OK")


def test_stale_evidence_reports_each_problem():
    report = Report()
    check_health(HEALTH_STALE, 1, report)
    assert report.problems == ["evidence: Injury / availability feed: last successful poll 120 minutes ago (stale after 90)"]


def test_an_unreachable_database_is_a_problem_not_a_silent_pass():
    report = Report()
    check_health(HEALTH_DB_ASLEEP, 1, report)
    assert len(report.problems) == 1 and report.problems[0].startswith("evidence-health could not run (exit 1)")
    assert "held asleep" in report.problems[0]


def test_only_due_windows_that_were_not_produced_are_problems():
    report = Report()
    rows = [
        _row("T-24h", True, "produced"),
        _row("T-4h", True, "missed_window"),
        _row("T-1h", True, ""),
        _row("T-30m", False, ""),  # not due yet: pending, not a problem
        _row("T-24h", False, "missed_window", game="10935", matchup="CLE@ATL"),  # before switch-on: never expected
        _row("T-4h", True, "stale", game="10935", matchup="CLE@ATL"),
    ]
    check_snapshots(rows, 0, report)
    assert report.problems == [
        "snapshot MEM@POR 2026-10-30T02:00Z T-4h: missed_window",
        "snapshot MEM@POR 2026-10-30T02:00Z T-1h: no snapshot",
        "snapshot CLE@ATL 2026-10-30T02:00Z T-4h: stale",
    ]
    cells = {g["matchup"]: g["cells"] for g in report.coverage}
    assert cells["MEM@POR"] == {"T-24h": "produced", "T-4h": "missed_window", "T-1h": "MISSING", "T-30m": "pending"}
    assert cells["CLE@ATL"]["T-24h"] == "missed_window"  # shown, but not a problem


def test_failed_queries_are_problems():
    report = Report()
    check_snapshots(None, 2, report)
    check_box_scores([], 1, report)
    assert report.problems == ["snapshot coverage query failed (exit 2)", "box-score coverage query failed (exit 1)"]


def test_missing_box_scores_are_problems():
    report = Report()
    check_box_scores([{"game_id": "1", "tipoff": "2026-10-29T00:00Z", "matchup": "CLE@ATL", "status": "final", "box_score_state": ""}], 0, report)
    assert report.problems == ["box score CLE@ATL 2026-10-29T00:00Z: game final, box score never fetched"]


@pytest.mark.parametrize(
    ("runs", "exit_code", "problem"),
    [
        ([{"createdAt": "2026-10-28T03:17:00Z"}], 0, None),
        ([{"createdAt": "2026-10-28T03:17:00Z"}, {"createdAt": "2026-10-20T03:17:00Z"}], 0, None),  # the latest counts
        ([{"createdAt": "2026-10-21T03:17:00Z"}], 0, "latest successful database backup is 9.0 days old (limit 8)"),
        ([], 0, "no successful database backup on record"),
        (None, 4, "could not list Backup runs (exit 4)"),
    ],
)
def test_backup_age(runs, exit_code, problem):
    report = Report()
    check_backups(runs, exit_code, NOW, 8, report)
    assert report.problems == ([problem] if problem else [])


def test_summary_shows_coverage_backup_and_evidence():
    report = Report()
    check_health(HEALTH_OK, 0, report)
    check_snapshots([_row(label, False, "") for label in watchdog.LABELS], 0, report)
    check_backups([{"createdAt": "2026-10-28T03:17:00Z"}], 0, NOW, 8, report)
    text = render_summary(report, NOW, "2026-10-29T01:28:47Z")
    assert text.startswith("## NBA watchdog: OK (2026-10-30 03:00Z)")
    assert "| MEM@POR (10937) | 2026-10-30T02:00Z | pending | pending | pending | pending |" in text
    assert "Latest successful database backup: 2026-10-28 03:17Z (2.0 days ago)" in text and "evidence health: OK" in text


def test_main_end_to_end(tmp_path):
    (tmp_path / "health.txt").write_text(HEALTH_OK, encoding="utf-8")
    (tmp_path / "snap.csv").write_text("game_id,tipoff,matchup,label,due,status\n10937,2026-10-30T02:00Z,MEM@POR,T-1h,t,missed_window\n", encoding="utf-8")
    (tmp_path / "box.csv").write_text("game_id,tipoff,matchup,status,box_score_state\n", encoding="utf-8")
    (tmp_path / "backups.json").write_text(json.dumps([{"createdAt": "2026-10-28T03:17:00Z"}]), encoding="utf-8")
    args = [
        "--health", str(tmp_path / "health.txt"), "--health-exit", "0",
        "--snapshots", str(tmp_path / "snap.csv"), "--snapshots-exit", "0",
        "--box-scores", str(tmp_path / "box.csv"), "--box-scores-exit", "0",
        "--backups", str(tmp_path / "backups.json"), "--backups-exit", "0",
        "--since", "2026-10-29T01:28:47Z", "--now", NOW.isoformat(),
        "--summary", str(tmp_path / "summary.md"), "--problems", str(tmp_path / "problems.txt"),
    ]
    assert watchdog.main(args) == 1
    assert (tmp_path / "problems.txt").read_text(encoding="utf-8") == "snapshot MEM@POR 2026-10-30T02:00Z T-1h: missed_window\n"
    (tmp_path / "snap.csv").write_text("game_id,tipoff,matchup,label,due,status\n10937,2026-10-30T02:00Z,MEM@POR,T-1h,t,produced\n", encoding="utf-8")
    assert watchdog.main(args) == 0 and (tmp_path / "problems.txt").read_text(encoding="utf-8") == ""


def test_missing_input_files_are_problems_not_crashes(tmp_path):
    missing = str(tmp_path / "absent")
    args = [
        "--health", missing, "--health-exit", "125", "--snapshots", missing, "--snapshots-exit", "1", "--box-scores", missing, "--box-scores-exit", "1",
        "--backups", missing, "--backups-exit", "1", "--since", "2026-10-29T01:28:47Z", "--now", NOW.isoformat(),
        "--summary", str(tmp_path / "s.md"), "--problems", str(tmp_path / "p.txt"),
    ]
    assert watchdog.main(args) == 1
    assert len((tmp_path / "p.txt").read_text(encoding="utf-8").splitlines()) == 4


# --- the SQL, on real PostgreSQL ------------------------------------------------


def _run_sql(db, name: str, since: datetime) -> list[dict]:
    conn = db.connection()
    conn.exec_driver_sql(f"set local watchdog.since = '{since.isoformat()}'")
    result = conn.exec_driver_sql((SQL_DIR / f"{name}.sql").read_text(encoding="utf-8"))
    return [dict(row._mapping) for row in result]


def _snapshot(db, game, label, offset, status):
    tip = game.scheduled_start
    db.add(
        NbaOutlookSnapshot(
            game_id=game.id, snapshot_label=label, offset_minutes=offset, scheduled_tipoff=tip, target_cutoff=tip - timedelta(minutes=offset),
            information_cutoff=tip - timedelta(minutes=offset), box_score_cutoff=tip - timedelta(minutes=offset + 240),
            generated_at=tip - timedelta(minutes=offset), status=status, roster_evidence={}, model_versions={}, counts={},
        )
    )


def _pg(db):
    if db.get_bind().dialect.name != "postgresql":
        pytest.skip("the watchdog SQL runs on the production PostgreSQL only")


def _games(db, now):
    home, away = NbaTeam(name="Home Team", abbreviation="HOM"), NbaTeam(name="Away Team", abbreviation="AWY")
    db.add_all([home, away])
    db.flush()

    def game(gid, tip, status="scheduled", season_type="regular", box=None):
        g = NbaGame(
            season_start_year=2026, season_type=season_type, game_date=tip.date(), scheduled_start=tip, status=status, box_score_state=box,
            home_team_id=home.id, away_team_id=away.id, source="espn", source_game_id=gid,
        )
        db.add(g)
        db.flush()
        return g

    return game


def test_expected_snapshots_sql(db_session):
    _pg(db_session)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    game = _games(db_session, now)
    tipped = game("tipped", now - timedelta(hours=2), status="final", box="ingested")  # all four windows due
    soon = game("soon", now + timedelta(hours=2))  # T-24h and T-4h due; T-1h, T-30m not yet
    game("pre", now + timedelta(hours=2), season_type="preseason")  # never checked
    game("ppd", now - timedelta(hours=2), status="postponed")  # never checked
    _snapshot(db_session, tipped, "T-24h", 1440, "produced")
    _snapshot(db_session, tipped, "T-4h", 240, "missed_window")
    _snapshot(db_session, tipped, "T-1h", 60, "stale")
    _snapshot(db_session, soon, "T-24h", 1440, "produced")
    db_session.flush()

    rows = _run_sql(db_session, "expected_snapshots", since=now - timedelta(days=2))
    assert {r["game_id"] for r in rows} == {tipped.id, soon.id} and {r["matchup"] for r in rows} == {"AWY@HOM"}
    by_game = {(r["game_id"], r["label"]): (r["due"], r["status"]) for r in rows}
    assert [by_game[(tipped.id, label)] for label in watchdog.LABELS] == [(True, "produced"), (True, "missed_window"), (True, "stale"), (True, "")]
    assert by_game[(soon.id, "T-24h")] == (True, "produced") and by_game[(soon.id, "T-4h")] == (True, "")
    assert by_game[(soon.id, "T-1h")] == (False, "") and by_game[(soon.id, "T-30m")] == (False, "")
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\dZ", r["tipoff"]) for r in rows)

    # Windows whose target cutoff came before snapshots were switched on are never due.
    later = _run_sql(db_session, "expected_snapshots", since=now)
    assert not any(r["due"] for r in later if r["game_id"] == tipped.id)


def test_box_scores_sql(db_session):
    _pg(db_session)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    game = _games(db_session, now)
    game("stored", now - timedelta(hours=10), status="final", box="ingested")
    game("partial", now - timedelta(hours=10), status="final", box="ingested_partial")
    missing = game("missing", now - timedelta(hours=10), status="final")
    stuck = game("stuck", now - timedelta(days=1), status="scheduled")  # never refreshed after tip-off
    game("recent", now - timedelta(hours=2), status="final")  # inside the 6-hour grace
    game("old", now - timedelta(days=5), status="final")  # outside the look-back
    game("ppd", now - timedelta(hours=10), status="postponed")
    db_session.flush()
    rows = _run_sql(db_session, "box_scores", since=now)
    assert {r["game_id"]: (r["status"], r["box_score_state"]) for r in rows} == {missing.id: ("final", ""), stuck.id: ("scheduled", "")}


# --- workflow --------------------------------------------------------------------


def test_watchdog_workflow_safeguards():
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "schedule:" in wf and "workflow_dispatch:" in wf
    assert "vars.NBA_WATCHDOG_ENABLED == 'true'" in wf  # scheduled runs are off until enabled
    assert "group: nba-watchdog" in wf and "group: nba-live-cycle" not in wf  # never blocks the live cycle
    assert "READONLY='-c default_transaction_read_only=on'" in wf
    assert wf.count('PGOPTIONS="$READONLY') == 2  # the evidence-health container and every psql check
    assert "python -m app.nba.cli evidence-health" in wf
    assert "issues: write" in wf and "actions: read" in wf and "contents: read" in wf
    assert 'SNAPSHOTS_SINCE: "2026-10-29T01:28:47Z"' in wf
    for forbidden in ("run-live-cycle", "outlooks.cli snapshot", "catch-up", "backfill", "alembic", "pg_restore", "gh variable", "vars.NBA_LIVE_CYCLE_ENABLED", "--force"):
        assert forbidden not in wf, forbidden


def test_watchdog_sql_is_read_only():
    for path in SQL_DIR.glob("*.sql"):
        sql = re.sub(r"--.*", "", path.read_text(encoding="utf-8")).upper()
        assert sql.strip().startswith(("WITH", "SELECT")), path.name
        for forbidden in ("INSERT", "UPDATE ", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "COPY", "SET "):
            assert forbidden not in sql, (path.name, forbidden)
