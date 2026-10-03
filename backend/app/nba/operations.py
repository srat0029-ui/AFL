"""The operational commands behind `python -m app.nba.cli`: running one live
cycle, reporting evidence health, and reporting schema status — written as
functions returning an exit code so the hosted job's behaviour is testable.

Exit codes for `run-live-cycle`, which decide whether the hosted job is
shown as failed:

  0  The cycle ran (some sources may have failed transiently - those are
     printed as warnings), or another cycle was already running.
  1  The cycle ran, but evidence collection is now stale: a source has been
     failing for longer than its tolerance. Sustained, not transient.
  2  A step failed for an internal reason (a bug or a database problem).
  3  The database schema is not at this code's migration head; nothing was
     run. Migrations are never applied automatically.

A single failed read of an unofficial source is expected now and then and
is not a reason to fail the job; it would make the job red often enough
that a real failure would be ignored. Sustained failure is caught by the
staleness check instead.
"""

import os
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.migration_status import migration_status
from app.nba.live_cycle import ERROR_INTERNAL, ERROR_SOURCE, STEP_FAILED, CycleAlreadyRunning, NbaPollingPolicy, run_live_cycle
from app.nba.monitoring import EvidenceHealth, evidence_health

EXIT_OK = 0
EXIT_EVIDENCE_STALE = 1
EXIT_INTERNAL_ERROR = 2
EXIT_SCHEMA_NOT_CURRENT = 3


def _annotate(level: str, message: str, out) -> None:
    """GitHub Actions turns these lines into visible run annotations."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level}::{message}", file=out)


def print_health(health: EvidenceHealth, out) -> None:
    print(f"evidence health: {'OK' if health.healthy else 'STALE'} (checked {health.checked_at.isoformat()})", file=out)
    print(
        f"  latest run: {health.latest_run_at.isoformat() if health.latest_run_at else 'never'} ({health.latest_run_status or '-'}); "
        f"latest fully successful run: {health.latest_successful_run_at.isoformat() if health.latest_successful_run_at else 'never'}",
        file=out,
    )
    print(f"  upcoming games: {health.upcoming_games}; in lineup window: {health.games_in_lineup_window}; current availability entries: {health.current_availability_entries}", file=out)
    for check in health.checks:
        when = f"{check.age_minutes:.0f} min ago" if check.age_minutes is not None else "never"
        state = "STALE" if check.stale else ("ok" if check.expected else "not expected now")
        print(f"  {check.label:<28} last success {when:<16} interval {check.interval_minutes:>5.0f} min  {state}", file=out)
    for problem in health.problems:
        print(f"  problem: {problem}", file=out)


def check_schema(db: Session, out) -> bool:
    status = migration_status(db)
    if status.is_current:
        return True
    message = (
        f"database schema is at {status.database_revision or 'no revision'}, this code needs {status.code_head}; "
        f"{len(status.pending)} migration(s) pending. Nothing was run - migrations are never applied automatically."
    )
    print(message, file=out)
    _annotate("error", message, out)
    return False


def run_cycle_command(
    db: Session,
    stats,
    evidence,
    *,
    source: str,
    force: bool = False,
    now: datetime | None = None,
    settings: Settings | None = None,
    check_schema_first: bool = True,
    out=None,
) -> int:
    settings = settings or get_settings()
    if check_schema_first and not check_schema(db, out):
        return EXIT_SCHEMA_NOT_CURRENT
    policy = NbaPollingPolicy.from_settings(settings)
    try:
        run = run_live_cycle(db, stats, evidence, source=source, policy=policy, now=now, force=force)
    except CycleAlreadyRunning as exc:
        print(f"skipped: {exc}", file=out)
        return EXIT_OK

    print(f"NBA live cycle run {run.id}: {run.status}", file=out)
    for step in run.steps:
        kind = f" ({step['error_kind']})" if step.get("error_kind") else ""
        print(f"  {step['step']:<18} {step['status']:<8}{kind:<11} {step['seconds']:>6}s  {step['detail']}", file=out)
        if step["status"] == STEP_FAILED:
            level = "error" if step.get("error_kind") == ERROR_INTERNAL else "warning"
            _annotate(level, f"{step['step']}: {step['detail']}", out)

    health = evidence_health(db, now=now, policy=policy, settings=settings)
    print_health(health, out)
    if any(step["status"] == STEP_FAILED and step.get("error_kind") == ERROR_INTERNAL for step in run.steps):
        return EXIT_INTERNAL_ERROR
    if not health.healthy:
        for problem in health.problems:
            _annotate("error", problem, out)
        return EXIT_EVIDENCE_STALE
    if any(step.get("error_kind") == ERROR_SOURCE for step in run.steps):
        _annotate("notice", "a source request failed this run; collection is still within tolerance", out)
    return EXIT_OK


def evidence_health_command(db: Session, *, now: datetime | None = None, settings: Settings | None = None, out=None) -> int:
    health = evidence_health(db, now=now, settings=settings)
    print_health(health, out)
    return EXIT_OK if health.healthy else EXIT_EVIDENCE_STALE


def schema_status_command(db: Session, out=None) -> int:
    status = migration_status(db)
    print(f"database revision: {status.database_revision or 'none'}", file=out)
    print(f"code head:         {status.code_head}", file=out)
    if status.is_current:
        print("schema is current", file=out)
        return EXIT_OK
    print(f"{len(status.pending)} migration(s) would be applied by `alembic upgrade head`:", file=out)
    for revision, description in status.pending:
        print(f"  {revision}  {description}", file=out)
    return EXIT_SCHEMA_NOT_CURRENT
