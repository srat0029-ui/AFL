"""Automatic scheduler that calls the existing run_live_cycle on a timer so
fixtures/results, player stats, settlement, projections, weather, and odds
stay current without a manual click/CLI-run each time.

Deliberately thin: every step this triggers already exists in
app.player_modelling.live_cycle.run_live_cycle (the same function the
`run-live-cycle` CLI command and the API's /refresh endpoint call) - this
module adds only pacing, single-instance locking, pause/resume, and
logging around that one call. No refresh/settlement/odds logic is
duplicated here.

Odds-quota safety is already enforced INSIDE run_live_cycle itself (see
prop_odds_quota.py's match-time-aware refresh interval - each odds step
reports "skipped, still within refresh interval" when it's not due,
regardless of how often run_live_cycle is called). Calling it too often
therefore can't over-spend paid API quota; the interval here exists to be
a good citizen of the free Squiggle/AFL Tables scrapers and to avoid
bloating the LiveCycleRun log with no-op runs.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.player_modelling.live_cycle import run_live_cycle

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOCK_PATH = BACKEND_ROOT / "live_cycle_scheduler.lock"
DEFAULT_PAUSE_PATH = BACKEND_ROOT / "live_cycle_scheduler.paused"
DEFAULT_LOG_PATH = BACKEND_ROOT / "live_cycle_scheduler.log"

# A floor on the configurable interval, independent of odds-quota safety
# (already handled inside run_live_cycle - see module docstring) - this
# exists purely so a misconfigured very-short interval doesn't hammer the
# free Squiggle/AFL Tables scrapers.
MIN_INTERVAL_MINUTES = 5

logger = logging.getLogger("live_cycle_scheduler")


def _configure_logging(log_path: Path) -> None:
    logger.handlers.clear()  # re-configurable per call (tests point this at a tmp path each time)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)


class SchedulerAlreadyRunningError(RuntimeError):
    pass


def _acquire_lock(lock_path: Path) -> None:
    """Exclusive-create the lock file - fails if it already exists, which
    is exactly the "another instance is already running" case this exists
    to catch. Within a single process, overlap is already structurally
    impossible (the loop below is strictly sequential: each tick awaits
    run_live_cycle before sleeping) - this lock only guards against a
    SECOND process being started."""
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SchedulerAlreadyRunningError(
            f"{lock_path} already exists — another scheduler instance appears to be running "
            "(or a previous one crashed without cleaning up). If you're sure no other instance "
            "is running, delete this file and retry."
        ) from None
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)


def _release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def pause(pause_path: Path = DEFAULT_PAUSE_PATH) -> None:
    """Creates the pause sentinel - a running scheduler checks for this
    once per tick and skips (without stopping the process, and without
    losing its lock) whenever it's present. Delete it (via resume()) to
    let ticks run again."""
    pause_path.touch(exist_ok=True)


def resume(pause_path: Path = DEFAULT_PAUSE_PATH) -> None:
    try:
        pause_path.unlink()
    except FileNotFoundError:
        pass


def is_paused(pause_path: Path = DEFAULT_PAUSE_PATH) -> bool:
    return pause_path.exists()


def run_scheduler_loop(
    interval_minutes: float | None = None,
    max_iterations: int | None = None,
    lock_path: Path = DEFAULT_LOCK_PATH,
    pause_path: Path = DEFAULT_PAUSE_PATH,
    log_path: Path = DEFAULT_LOG_PATH,
) -> int:
    """Runs run_live_cycle on a timer until interrupted (Ctrl+C) or, in
    tests only, until max_iterations ticks have happened. lock_path/
    pause_path/log_path default to fixed real-usage locations but are
    overridable so tests never touch those real files. See module
    docstring for the locking/pacing/quota reasoning."""
    _configure_logging(log_path)
    settings = get_settings()
    interval = interval_minutes if interval_minutes is not None else settings.live_cycle_interval_minutes
    if interval < MIN_INTERVAL_MINUTES:
        logger.warning(
            "Interval %.1fm is below the %dm floor (kept as a good-citizen minimum for the free "
            "Squiggle/AFL Tables scrapers this calls into) — using %dm instead.",
            interval, MIN_INTERVAL_MINUTES, MIN_INTERVAL_MINUTES,
        )
        interval = MIN_INTERVAL_MINUTES

    _acquire_lock(lock_path)
    logger.info(
        "Live cycle scheduler started (pid=%d, interval=%.1fm). Lock: %s. "
        "To pause without stopping this process: create %s (delete it to resume). Ctrl+C to stop.",
        os.getpid(), interval, lock_path, pause_path,
    )
    iterations = 0
    try:
        while max_iterations is None or iterations < max_iterations:
            if is_paused(pause_path):
                logger.info("paused (%s exists) — skipping this tick", pause_path)
            else:
                db = SessionLocal()
                try:
                    run = run_live_cycle(db)
                    logger.info(
                        "live cycle run %s — %s — matches_affected=%d quotes_added=%d "
                        "observations_added=%d observations_settled=%d",
                        run.id, run.overall_status.upper(), run.matches_affected,
                        run.quotes_added, run.observations_added, run.observations_settled,
                    )
                except Exception:  # noqa: BLE001 - one bad cycle must not kill the scheduler
                    logger.exception("live cycle run failed (will retry next tick)")
                finally:
                    db.close()
            iterations += 1
            if max_iterations is None or iterations < max_iterations:
                time.sleep(interval * 60)
    finally:
        _release_lock(lock_path)
    return 0
