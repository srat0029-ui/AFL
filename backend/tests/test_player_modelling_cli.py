"""Focused tests for app/player_modelling/cli.py's run-live-cycle reporting
path - specifically the defensive fallback added after a real production
incident (LiveCycleRun id=3): a durable audit write failed inside
run_live_cycle(), its own best-effort rollback/refresh also failed
silently, and the returned ORM row's attributes were left
expired/unloadable - so the CLI's own reporting code raised a SECOND,
unrelated exception while trying to print a summary, masking the original
fail-closed result behind a generic uncaught crash (GitHub saw exit code 1
instead of the intended controlled 0/1/2). These tests never touch a real
database - `run_live_cycle` and `SessionLocal` are both monkeypatched.
"""

import app.player_modelling.cli as cli_module


class _FakeDb:
    """Stand-in for a SQLAlchemy Session - only close() is ever called on
    it directly in _run_live_cycle()'s own code."""

    def close(self):
        pass


class _FakeRun:
    """Stand-in for a LiveCycleRun ORM row. Every attribute access is
    recorded in `accessed`, in order, so a test can assert exactly which
    attributes the reporting code touched - the fallback path must not
    touch anything beyond whatever already succeeded before a simulated
    failure. `fail_on` (if set) raises `exc` the first time that attribute
    name is accessed; every other attribute is served from `values`."""

    def __init__(self, values: dict, fail_on: str | None = None, exc: Exception | None = None):
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_fail_on", fail_on)
        object.__setattr__(self, "_exc", exc or RuntimeError("simulated failure"))
        object.__setattr__(self, "accessed", [])

    def __getattr__(self, name):
        self.accessed.append(name)
        if name == self._fail_on:
            raise self._exc
        return self._values[name]


_BASE_VALUES = dict(
    id=42,
    overall_status="ok",
    steps=[{"status": "success", "step": "refresh_fixtures", "detail": "5 fixtures seen"}],
    matches_affected=2,
    quotes_added=10,
    observations_added=3,
    observations_settled=1,
    team_odds_quotes_added=4,
    weather_snapshots_added=1,
    odds_credits_consumed=None,
    odds_credits_remaining=None,
)


def _patch(monkeypatch, run):
    monkeypatch.setattr(cli_module, "SessionLocal", lambda: _FakeDb())
    monkeypatch.setattr(cli_module, "run_live_cycle", lambda db: run)


def test_normal_ok_reporting_is_unchanged(monkeypatch, capsys):
    run = _FakeRun(_BASE_VALUES)
    _patch(monkeypatch, run)

    exit_code = cli_module._run_live_cycle()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Live cycle run 42 — overall status: OK" in out
    assert "matches affected: 2" in out
    assert "WARNING" not in out


def test_normal_partial_reporting_returns_one(monkeypatch, capsys):
    values = {**_BASE_VALUES, "overall_status": "partial"}
    run = _FakeRun(values)
    _patch(monkeypatch, run)

    assert cli_module._run_live_cycle() == 1


def test_normal_blocked_reporting_returns_two(monkeypatch, capsys):
    values = {**_BASE_VALUES, "overall_status": "blocked"}
    run = _FakeRun(values)
    _patch(monkeypatch, run)

    assert cli_module._run_live_cycle() == 2


def test_in_progress_status_prints_stale_run_warning_and_returns_two(monkeypatch, capsys):
    values = {**_BASE_VALUES, "overall_status": "in_progress"}
    run = _FakeRun(values)
    _patch(monkeypatch, run)

    exit_code = cli_module._run_live_cycle()

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "did not reach a normal finish" in out
    assert "LiveCycleRun id=42" in out


def test_reporting_failure_does_not_raise_and_returns_failure_exit_code(monkeypatch, capsys):
    """The core regression test for the production incident: a real
    ORM/connection exception raised while READING the run's attributes
    (simulating an expired row after a failed rollback/refresh) must never
    propagate as an uncaught exception, and must never be reported as a
    success."""
    run = _FakeRun(_BASE_VALUES, fail_on="overall_status", exc=RuntimeError("connection is closed"))
    _patch(monkeypatch, run)

    exit_code = cli_module._run_live_cycle()  # must not raise

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "did not finish normally" in out
    assert "detailed reporting could not be completed" in out
    assert "connection is closed" in out


def test_reporting_failure_preserves_the_run_id_captured_before_the_failure(monkeypatch, capsys):
    run = _FakeRun(_BASE_VALUES, fail_on="steps", exc=RuntimeError("server closed the connection unexpectedly"))
    _patch(monkeypatch, run)

    exit_code = cli_module._run_live_cycle()

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "Live Cycle run 42 did not finish normally" in out


def test_reporting_failure_before_id_is_captured_omits_the_id_rather_than_guessing(monkeypatch, capsys):
    run = _FakeRun(_BASE_VALUES, fail_on="id", exc=RuntimeError("connection is closed"))
    _patch(monkeypatch, run)

    exit_code = cli_module._run_live_cycle()

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "Live Cycle run did not finish normally" in out
    assert "Live Cycle run 42" not in out


def test_fallback_path_touches_nothing_beyond_the_point_of_failure(monkeypatch):
    """Stands in for 'the fallback path issues no additional database
    access': once an attribute access raises, nothing else on the ORM
    object is touched - proven here by recording every attribute name
    actually accessed and asserting the list stops exactly at the failure."""
    run = _FakeRun(_BASE_VALUES, fail_on="overall_status", exc=RuntimeError("connection is closed"))
    _patch(monkeypatch, run)

    cli_module._run_live_cycle()

    assert run.accessed == ["id", "overall_status"]


def test_odds_credits_reporting_still_works_when_present(monkeypatch, capsys):
    values = {**_BASE_VALUES, "odds_credits_consumed": 7, "odds_credits_remaining": 493}
    run = _FakeRun(values)
    _patch(monkeypatch, run)

    cli_module._run_live_cycle()

    out = capsys.readouterr().out
    assert "requests_used=7 requests_remaining=493" in out
