"""Tests for the automatic live-cycle scheduler (pacing, single-instance
locking, pause/resume, and failure isolation around the existing
run_live_cycle - see app/player_modelling/scheduler.py's module docstring).
Every test points lock/pause/log paths at tmp_path and fakes run_live_cycle
itself, so nothing here touches a real database or the real scheduler
lock/log files a developer might have running."""

import app.player_modelling.scheduler as scheduler_module
from app.player_modelling.scheduler import (
    SchedulerAlreadyRunningError,
    is_paused,
    pause,
    resume,
    run_scheduler_loop,
)


class _FakeSession:
    def close(self):
        pass


class _FakeRun:
    def __init__(self, run_id=1, status="ok"):
        self.id = run_id
        self.overall_status = status
        self.matches_affected = 1
        self.quotes_added = 0
        self.observations_added = 0
        self.observations_settled = 0


def _paths(tmp_path):
    return dict(lock_path=tmp_path / "s.lock", pause_path=tmp_path / "s.paused", log_path=tmp_path / "s.log")


def _patch_common(monkeypatch, calls):
    def fake_run_live_cycle(db):
        calls.append(db)
        return _FakeRun(run_id=len(calls))

    monkeypatch.setattr(scheduler_module, "run_live_cycle", fake_run_live_cycle)
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(scheduler_module.time, "sleep", lambda seconds: None)


def test_calls_run_live_cycle_once_per_tick(tmp_path, monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls)

    run_scheduler_loop(interval_minutes=10, max_iterations=3, **_paths(tmp_path))

    assert len(calls) == 3


def test_lock_released_after_loop_ends(tmp_path, monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls)
    paths = _paths(tmp_path)

    run_scheduler_loop(interval_minutes=10, max_iterations=1, **paths)

    assert not paths["lock_path"].exists()


def test_refuses_to_start_when_lock_already_held(tmp_path, monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls)
    paths = _paths(tmp_path)
    paths["lock_path"].write_text("12345")  # simulates another instance already running

    try:
        run_scheduler_loop(interval_minutes=10, max_iterations=1, **paths)
        assert False, "expected SchedulerAlreadyRunningError"
    except SchedulerAlreadyRunningError:
        pass

    assert calls == []  # never even attempted a cycle
    assert paths["lock_path"].read_text() == "12345"  # untouched, not clobbered


def test_paused_tick_skips_run_live_cycle_without_losing_the_lock(tmp_path, monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls)
    paths = _paths(tmp_path)
    pause(pause_path=paths["pause_path"])
    assert is_paused(pause_path=paths["pause_path"])

    run_scheduler_loop(interval_minutes=10, max_iterations=2, **paths)

    assert calls == []
    assert not paths["lock_path"].exists()  # still released cleanly at the end despite skipping every tick


def test_resume_lets_ticks_run_again(tmp_path, monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls)
    paths = _paths(tmp_path)
    pause(pause_path=paths["pause_path"])
    resume(pause_path=paths["pause_path"])
    assert not is_paused(pause_path=paths["pause_path"])

    run_scheduler_loop(interval_minutes=10, max_iterations=2, **paths)

    assert len(calls) == 2


def test_a_failed_cycle_does_not_stop_the_scheduler(tmp_path, monkeypatch):
    calls = []

    def failing_then_ok(db):
        calls.append(db)
        if len(calls) == 1:
            raise RuntimeError("Squiggle temporarily unavailable")
        return _FakeRun(run_id=len(calls))

    monkeypatch.setattr(scheduler_module, "run_live_cycle", failing_then_ok)
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(scheduler_module.time, "sleep", lambda seconds: None)
    paths = _paths(tmp_path)

    run_scheduler_loop(interval_minutes=10, max_iterations=2, **paths)

    assert len(calls) == 2  # the failure on tick 1 didn't stop tick 2 from happening
    assert not paths["lock_path"].exists()  # lock still released even though a tick raised


def test_interval_below_floor_is_clamped_not_rejected(tmp_path, monkeypatch):
    calls = []
    _patch_common(monkeypatch, calls)
    slept = []
    monkeypatch.setattr(scheduler_module.time, "sleep", lambda seconds: slept.append(seconds))

    run_scheduler_loop(interval_minutes=0.5, max_iterations=2, **_paths(tmp_path))

    assert len(calls) == 2
    assert slept == [scheduler_module.MIN_INTERVAL_MINUTES * 60]  # clamped up to the floor, not left at 0.5m
