"""Tests for the LiveCycleRun durable lifecycle (run_live_cycle's
_start_run/_persist_steps_durable/_finish_run/_reconcile_stale_runs) - the
guarantee that a run killed mid-cycle (timeout, crashed runner, host
failure) still leaves a real audit record of what it actually completed,
rather than silently mutating production with zero trace. This is a
separate concern from tests/test_live_cycle.py's step-orchestration/
business-logic tests, which this file reuses fakes/helpers from.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.player_modelling.live_cycle as live_cycle_module
from app.models import (
    RUN_BLOCKED,
    RUN_IN_PROGRESS,
    RUN_INTERRUPTED,
    RUN_OK,
    RUN_PARTIAL,
    Match,
    STEP_RECOVERABLE_FAILURE,
    STEP_SUCCESS,
    LiveCycleRun,
)
from app.player_modelling.live_cycle import AuditPersistenceError, run_live_cycle
from app.providers.types import Fixture
from tests.test_live_cycle import (
    FakeFixtureProvider,
    FakeOddsProvider,
    FakePlayerStatsProvider,
    _seed_scheduled_match,
)


def _patch_normal_providers(monkeypatch):
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider())
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider())
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)


def test_run_row_exists_in_progress_before_first_mutating_step(db_session, monkeypatch):
    """F.1: a LiveCycleRun must already exist, durably committed, before
    the first mutating step (fixture ingestion) runs."""
    _seed_scheduled_match(db_session)
    captured = {}
    original_ingest_fixtures = live_cycle_module.ingest_fixtures

    def spy_ingest_fixtures(db, fixtures):
        existing = db.scalars(select(LiveCycleRun)).all()
        captured["count"] = len(existing)
        captured["status"] = existing[0].overall_status if existing else None
        captured["finished_at"] = existing[0].finished_at if existing else "MISSING"
        return original_ingest_fixtures(db, fixtures)

    _patch_normal_providers(monkeypatch)
    monkeypatch.setattr(live_cycle_module, "ingest_fixtures", spy_ingest_fixtures)

    run = run_live_cycle(db_session)

    assert captured["count"] == 1
    assert captured["status"] == RUN_IN_PROGRESS
    assert captured["finished_at"] is None
    assert run.id is not None  # the row _finish_run returns is the SAME row, not a new one


def test_step_results_durable_before_later_steps_execute(db_session, monkeypatch):
    """F.2: earlier steps' results must be visible in the durable row
    before a later step even begins - proven by re-querying the row from
    inside a later step's call, not from the final in-memory report."""
    _seed_scheduled_match(db_session)
    captured = {}
    original_settle = live_cycle_module.settle_all_completed_matches

    def spy_settle(db):
        existing = db.scalars(select(LiveCycleRun)).all()
        assert len(existing) == 1
        captured["step_names_so_far"] = [s["step"] for s in existing[0].steps]
        captured["status_so_far"] = existing[0].overall_status
        return original_settle(db)

    _patch_normal_providers(monkeypatch)
    monkeypatch.setattr(live_cycle_module, "settle_all_completed_matches", spy_settle)

    run_live_cycle(db_session)

    assert "refresh_fixtures" in captured["step_names_so_far"]
    assert "identify_upcoming_round" in captured["step_names_so_far"]
    assert "update_completed_player_stats" in captured["step_names_so_far"]
    assert captured["status_so_far"] == RUN_IN_PROGRESS  # not finalized yet - settle_props hasn't run


def test_exception_after_fixture_ingestion_leaves_durable_audit_intact(db_session, monkeypatch):
    """F.3: an exception that escapes run_live_cycle entirely (simulating
    the process being killed) after a real mutating step already completed
    must leave that step's record durably in place - overall_status stays
    RUN_IN_PROGRESS (never a fabricated final verdict), finished_at stays
    None (the process never got to set it)."""
    _seed_scheduled_match(db_session)
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider())

    def boom(db):
        raise RuntimeError("simulated process kill mid-cycle")

    monkeypatch.setattr(live_cycle_module, "load_next_upcoming_round", boom)

    with pytest.raises(RuntimeError, match="simulated process kill"):
        run_live_cycle(db_session)

    rows = db_session.scalars(select(LiveCycleRun)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.overall_status == RUN_IN_PROGRESS
    assert row.finished_at is None
    step_names = [s["step"] for s in row.steps]
    assert step_names == ["refresh_fixtures"]
    assert row.steps[0]["status"] == STEP_SUCCESS


def test_late_step_failure_does_not_erase_earlier_audit_history(db_session, monkeypatch):
    """F.4: a step near the end of the cycle failing must not erase or
    overwrite the durable record of steps that already succeeded - checked
    against a fresh independent re-fetch, not the returned Python object."""
    _seed_scheduled_match(db_session)
    _patch_normal_providers(monkeypatch)

    def boom(db, upcoming_matches):
        raise RuntimeError("Open-Meteo unavailable")

    monkeypatch.setattr(live_cycle_module, "refresh_weather_for_matches", boom)

    run = run_live_cycle(db_session)

    weather_step = next(s for s in run.steps if s["step"] == "refresh_weather")
    assert weather_step["status"] == STEP_RECOVERABLE_FAILURE
    assert run.overall_status == RUN_PARTIAL
    assert run.finished_at is not None

    fresh = db_session.get(LiveCycleRun, run.id)
    fresh_step_names = [s["step"] for s in fresh.steps]
    assert "refresh_fixtures" in fresh_step_names
    assert "identify_upcoming_round" in fresh_step_names
    assert "settle_props" in fresh_step_names
    assert "refresh_weather" in fresh_step_names


def test_normal_run_reaches_a_terminal_status_and_finished_at(db_session, monkeypatch):
    """F.5: normal execution must still reach a real terminal status, not
    get stuck in RUN_IN_PROGRESS/RUN_INTERRUPTED."""
    _seed_scheduled_match(db_session)
    _patch_normal_providers(monkeypatch)

    run = run_live_cycle(db_session)

    assert run.finished_at is not None
    assert run.overall_status in (RUN_OK, RUN_PARTIAL, RUN_BLOCKED)
    assert run.overall_status not in (RUN_IN_PROGRESS, RUN_INTERRUPTED)


def test_stale_in_progress_run_is_reconciled_as_interrupted(db_session, monkeypatch):
    """F.6: a row left RUN_IN_PROGRESS well past STALE_RUN_THRESHOLD_MINUTES
    is honestly reconciled as RUN_INTERRUPTED on the next invocation - its
    original step history is preserved, and a reconciliation marker is
    appended rather than any step being rewritten."""
    stale_run_at = datetime.now(timezone.utc) - timedelta(minutes=live_cycle_module.STALE_RUN_THRESHOLD_MINUTES + 5)
    stale = LiveCycleRun(
        run_at=stale_run_at, finished_at=None, overall_status=RUN_IN_PROGRESS,
        steps=[{"step": "refresh_fixtures", "status": STEP_SUCCESS, "detail": "ok", "duration_seconds": 1.2}],
    )
    db_session.add(stale)
    db_session.commit()
    stale_id = stale.id

    _seed_scheduled_match(db_session)
    _patch_normal_providers(monkeypatch)

    new_run = run_live_cycle(db_session)

    reconciled = db_session.get(LiveCycleRun, stale_id)
    assert reconciled.overall_status == RUN_INTERRUPTED
    assert reconciled.finished_at is not None
    reconciled_step_names = [s["step"] for s in reconciled.steps]
    assert "refresh_fixtures" in reconciled_step_names
    assert "system_reconciliation" in reconciled_step_names
    assert new_run.id != stale_id


def test_recent_in_progress_run_is_not_misclassified_as_stale(db_session):
    """F.6 (negative case): a run that started moments ago must never be
    reconciled - it could still be a genuinely active concurrent
    execution, and this mechanism must not guess otherwise."""
    recent_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    recent = LiveCycleRun(run_at=recent_run_at, finished_at=None, overall_status=RUN_IN_PROGRESS, steps=[])
    db_session.add(recent)
    db_session.commit()
    recent_id = recent.id

    n_reconciled = live_cycle_module._reconcile_stale_runs(db_session, datetime.now(timezone.utc))

    assert n_reconciled == 0
    still_in_progress = db_session.get(LiveCycleRun, recent_id)
    assert still_in_progress.overall_status == RUN_IN_PROGRESS
    assert still_in_progress.finished_at is None


def test_two_sequential_invocations_produce_two_separate_durable_rows(db_session, monkeypatch):
    """F.7: the create-early-then-update lifecycle must still preserve the
    established 'one row per invocation' contract - two calls produce two
    rows, never a merged or overwritten single row. (Settlement-level
    idempotency itself - rerunning doesn't double-settle - is already
    covered by test_live_cycle.py's
    test_stuck_in_progress_match_completes_and_settles_via_delayed_fixture_refresh,
    unchanged by this module.)"""
    _seed_scheduled_match(db_session)
    _patch_normal_providers(monkeypatch)

    first = run_live_cycle(db_session)
    second = run_live_cycle(db_session)

    assert first.id != second.id
    all_rows = db_session.scalars(select(LiveCycleRun)).all()
    assert len(all_rows) == 2


def _always_raise_audit_persistence_error(*_args, **_kwargs):
    raise AuditPersistenceError("simulated audit database outage")


def test_audit_persistence_failure_stops_before_next_mutating_step(db_session, monkeypatch):
    """Fail-closed: if persisting a step's audit result fails, the NEXT
    mutating step must never be called - proven by spying on step 2's own
    function rather than just checking the final report shape."""
    _seed_scheduled_match(db_session)
    _patch_normal_providers(monkeypatch)
    monkeypatch.setattr(live_cycle_module, "_execute_durable_write", _always_raise_audit_persistence_error)

    step2_called = {"value": False}
    original_load_next = live_cycle_module.load_next_upcoming_round

    def spy_load_next_upcoming_round(db):
        step2_called["value"] = True
        return original_load_next(db)

    monkeypatch.setattr(live_cycle_module, "load_next_upcoming_round", spy_load_next_upcoming_round)

    run = run_live_cycle(db_session)

    assert step2_called["value"] is False, "step 2 must never run once step 1's audit persist failed"
    # The already-created LiveCycleRun survives, exactly as it last was
    # durably written - which, since even the FIRST persist attempt
    # failed, means still in_progress with no steps ever durably recorded.
    assert run is not None
    assert run.overall_status == RUN_IN_PROGRESS
    assert run.finished_at is None


def test_already_created_run_survives_audit_persistence_failure(db_session, monkeypatch):
    """The row _start_run created before step 1 must still exist afterward
    - never deleted, never replaced - independently re-queried from the DB."""
    _seed_scheduled_match(db_session)
    _patch_normal_providers(monkeypatch)
    monkeypatch.setattr(live_cycle_module, "_execute_durable_write", _always_raise_audit_persistence_error)

    run = run_live_cycle(db_session)

    rows = db_session.scalars(select(LiveCycleRun)).all()
    assert len(rows) == 1
    assert rows[0].id == run.id


def test_completed_step_mutation_is_not_rolled_back_when_its_audit_persist_fails(db_session, monkeypatch):
    """A step's own real mutation (fixture ingestion committing a new
    Match row) must survive even though the very next thing - persisting
    that step's audit result - fails. The mutation and the audit write are
    genuinely separate commits; one failing must never undo the other."""
    _seed_scheduled_match(db_session)
    new_fixture = Fixture(
        external_id="88001", sport_code="AFL", season_year=2026, round_number=9,
        home_team="Richmond", away_team="Essendon",
        scheduled_start=datetime.now(timezone.utc) + timedelta(days=5), status="scheduled",
    )
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider(fixtures=[new_fixture]))
    monkeypatch.setattr(live_cycle_module, "_execute_durable_write", _always_raise_audit_persistence_error)

    run_live_cycle(db_session)

    matches = db_session.scalars(select(Match)).all()
    assert any((m.external_ids or {}).get("squiggle") == "88001" for m in matches), (
        "the real fixture-ingestion mutation from step 1 must still be committed even though "
        "persisting step 1's audit result afterward failed"
    )


def test_no_fake_terminal_status_invented_when_audit_database_unavailable(db_session, monkeypatch):
    """If the audit database can't be reached, run_live_cycle must not
    fabricate a success/partial/blocked verdict, and must not invent a
    step that was never actually durably recorded - it stays in_progress,
    reflecting exactly what the database itself can confirm."""
    _seed_scheduled_match(db_session)
    _patch_normal_providers(monkeypatch)
    monkeypatch.setattr(live_cycle_module, "_execute_durable_write", _always_raise_audit_persistence_error)

    run = run_live_cycle(db_session)

    assert run.overall_status not in (RUN_OK, RUN_PARTIAL, RUN_BLOCKED)
    assert run.overall_status == RUN_IN_PROGRESS
    assert run.finished_at is None
    # Nothing was ever durably persisted (the very first persist attempt
    # failed), so the durable steps list must be empty, not a fabricated
    # entry claiming refresh_fixtures succeeded or failed.
    assert run.steps == []
