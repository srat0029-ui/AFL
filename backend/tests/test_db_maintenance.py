"""The guarded production-migration helper and its workflow."""

from io import StringIO
from pathlib import Path

import app.db_maintenance as dbm
from app.migration_status import MigrationStatus

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "db-migrate.yml"


def test_compare_passes_when_existing_tables_are_untouched_and_lists_new_ones():
    before = {"revision": "a", "row_counts": {"matches": 10, "players": 5, "alembic_version": 1}}
    after = {"revision": "b", "row_counts": {"matches": 10, "players": 5, "alembic_version": 1, "nba_games": 0, "nba_teams": 0}}
    ok, lines = dbm.compare(before, after)
    assert ok
    assert "revision: a -> b" in lines and "tables created: 2" in lines
    assert "  + nba_games (0 rows)" in lines and "pre-existing tables with identical row counts: 2" in lines


def test_compare_fails_when_an_existing_table_changes_or_disappears():
    before = {"revision": "a", "row_counts": {"matches": 10, "players": 5}}
    ok, lines = dbm.compare(before, {"revision": "b", "row_counts": {"matches": 9}})
    assert not ok
    assert "ROW COUNT CHANGED: matches 10 -> 9" in lines and "MISSING AFTER MIGRATION: players (had 5 rows)" in lines


def test_upgrade_refuses_unless_the_database_is_at_the_expected_revision(monkeypatch):
    monkeypatch.setattr(dbm, "migration_status", lambda db: MigrationStatus("04b59a8a329c", "90343a82a7c8", [("1e453acf52cb", "x")], False))
    called = []
    monkeypatch.setattr(dbm.command, "upgrade", lambda *a, **k: called.append(a))
    out = StringIO()
    assert dbm.upgrade("75715f635e7a", out=out) == 2
    assert called == [] and "REFUSED" in out.getvalue() and "Nothing was changed" in out.getvalue()


def test_upgrade_runs_alembic_only_from_the_expected_revision(monkeypatch):
    statuses = iter([
        MigrationStatus("75715f635e7a", "90343a82a7c8", [("04b59a8a329c", "a"), ("90343a82a7c8", "b")], False),
        MigrationStatus("90343a82a7c8", "90343a82a7c8", [], True),
    ])
    monkeypatch.setattr(dbm, "migration_status", lambda db: next(statuses))
    called = []
    monkeypatch.setattr(dbm.command, "upgrade", lambda config, target: called.append(target))
    out = StringIO()
    assert dbm.upgrade("75715f635e7a", out=out) == 0
    assert called == ["head"] and "database revision after upgrade: 90343a82a7c8" in out.getvalue()


def test_migration_workflow_is_manual_only_and_guarded():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow and "schedule:" not in workflow and "push:" not in workflow
    assert '"$CONFIRM" != "migrate"' in workflow
    assert "--expect-current" in workflow and "app.db_maintenance compare" in workflow
    assert "group: nba-live-cycle" in workflow  # never alongside a live cycle
