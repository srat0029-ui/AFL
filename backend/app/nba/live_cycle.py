"""The NBA live evidence cycle: one invocation refreshes whatever is due
and records what the source shows right now. Safe to run as often as you
like, by hand or on a schedule (the hosted schedule wakes it every 15
minutes - see .github/workflows/nba-live-cycle.yml).

What a run does, in order:

1. schedule          refresh games in a window around today; a changed status
                     or tip-off time is appended as a schedule observation
2. availability      read the league injury feed and append what changed
3. team_rosters      each team's listed roster
4. team_depth_charts each team's depth chart
5. game_lineups      the listed lineup for each team in games approaching
                     tip-off, polled more often the closer tip-off is
6. box_scores        ingest box scores for games that have become final

It collects evidence only. No model runs, no prediction is made, no odds
are fetched and no API quota is spent.

Polling frequency
-----------------
Every interval comes from NbaPollingPolicy, which is built from settings.
The cycle being woken does not mean anything is fetched: each step checks
when it last succeeded and is skipped as "not due" until its interval has
passed. Pregame lineups use tiers by time-to-tip (default: none beyond 24
hours, hourly from 24 to 4 hours, every 30 minutes from 4 to 1 hour, every
15 minutes in the final hour, nothing after tip-off) - the point being to
discover when the source starts publishing starters. `force=True` ignores
every interval for one run.

One run at a time
-----------------
Two cycles must never write the same evidence at once. On PostgreSQL a
session-level advisory lock is held on a dedicated connection for the whole
run; a second process that cannot take it does nothing. On other databases
(local SQLite) a recent run still marked in progress has the same effect.
Either way the evidence writes are themselves idempotent, so even an
overlap that slipped past both would not duplicate a row.

Failure and interruption
------------------------
Every step is independent and every write inside one is its own
transaction, so a failing source never erases or rewrites earlier
observations; it simply means nothing new was recorded, and the step is
due again next run. Failures are classified:

- "source": the provider could not be read or returned something not
  credible (network error, HTTP error, unexpected shape, a suspiciously
  shrunken feed). Expected occasionally from an unofficial source; the run
  is "partial".
- "internal": anything else - a bug or a database problem. The run is
  "failed" and the hosted job fails visibly.

There are no retries inside a run. The next scheduled wake-up is the retry,
which keeps the request rate to the source predictable.
"""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.prospective import ensure_utc
from app.models.nba import NbaEvidencePoll, NbaGame, NbaGameStatus, NbaLiveCycleRun
from app.models.nba.evidence import (
    POLL_AVAILABILITY,
    POLL_BOX_SCORES,
    POLL_GAME_LINEUP,
    POLL_SCHEDULE,
    POLL_TEAM_DEPTH_CHART,
    POLL_TEAM_ROSTER,
    RUN_FAILED,
    RUN_IN_PROGRESS,
    RUN_INTERRUPTED,
    RUN_OK,
    RUN_PARTIAL,
)
from app.nba.evidence import EvidenceRejected, lineup_scope, record_availability, record_game_lineup, record_team_depth_chart, record_team_roster
from app.nba.ingestion import NbaStatsProvider, sync_box_scores, sync_schedule, sync_teams, teams_by_source_id
from app.providers.nba.espn import EspnNbaError
from app.providers.nba.evidence_types import NbaDepthChart, NbaGameLineup, NbaInjuryFeed, NbaTeamRoster

logger = logging.getLogger(__name__)

STEP_OK = "ok"
STEP_SKIPPED = "skipped"
STEP_FAILED = "failed"

ERROR_SOURCE = "source"
ERROR_INTERNAL = "internal"

# Errors that mean "the source could not be read, or what it returned is not
# credible" - expected now and then from an unofficial source, and never a
# reason to fail the hosted job.
SOURCE_ERRORS: tuple[type[BaseException], ...] = (EspnNbaError, EvidenceRejected, httpx.HTTPError)

# Arbitrary but fixed: identifies this cycle's PostgreSQL advisory lock.
ADVISORY_LOCK_KEY = 7_315_003_001


class NbaEvidenceProvider(Protocol):
    def get_injuries(self) -> NbaInjuryFeed: ...

    def get_team_roster(self, source_team_id: str) -> NbaTeamRoster: ...

    def get_team_depth_chart(self, source_team_id: str, season_start_year: int) -> NbaDepthChart: ...

    def get_game_lineups(self, source_game_id: str, source_team_ids: list[str]) -> list[NbaGameLineup]: ...


class CycleAlreadyRunning(Exception):
    """Another live cycle holds the lock; this one did nothing."""


# --- policy ----------------------------------------------------------------


def parse_lineup_tiers(spec: str) -> tuple[tuple[timedelta, timedelta], ...]:
    """Parse "24:60,4:30,1:15" into ((24h, 60min), (4h, 30min), (1h, 15min)),
    sorted by hours-before-tip, largest first. Raises ValueError on anything
    malformed rather than silently polling at a guessed rate."""
    tiers: list[tuple[timedelta, timedelta]] = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        try:
            hours, minutes = (float(x) for x in part.split(":"))
        except ValueError as exc:
            raise ValueError(f"lineup poll tier {part!r} is not '<hours>:<minutes>'") from exc
        if hours <= 0 or minutes <= 0:
            raise ValueError(f"lineup poll tier {part!r} must have positive hours and minutes")
        tiers.append((timedelta(hours=hours), timedelta(minutes=minutes)))
    if not tiers:
        raise ValueError("at least one lineup poll tier is required")
    tiers.sort(key=lambda tier: tier[0], reverse=True)
    if len({bound for bound, _ in tiers}) != len(tiers):
        raise ValueError("lineup poll tiers must have distinct hour bounds")
    return tuple(tiers)


@dataclass(frozen=True)
class NbaPollingPolicy:
    """Every polling decision the cycle makes, in one place."""

    availability_interval: timedelta = timedelta(minutes=30)
    schedule_interval: timedelta = timedelta(hours=3)
    roster_interval: timedelta = timedelta(hours=24)
    depth_chart_interval: timedelta = timedelta(hours=24)
    box_score_interval: timedelta = timedelta(hours=1)
    schedule_lookback_days: int = 3
    schedule_lookahead_days: int = 14
    lineup_tiers: tuple[tuple[timedelta, timedelta], ...] = parse_lineup_tiers("24:60,4:30,1:15")
    stale_run_after: timedelta = timedelta(minutes=40)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "NbaPollingPolicy":
        s = settings or get_settings()
        return cls(
            availability_interval=timedelta(minutes=s.nba_poll_availability_minutes),
            schedule_interval=timedelta(minutes=s.nba_poll_schedule_minutes),
            roster_interval=timedelta(minutes=s.nba_poll_team_rosters_minutes),
            depth_chart_interval=timedelta(minutes=s.nba_poll_depth_charts_minutes),
            box_score_interval=timedelta(minutes=s.nba_poll_box_scores_minutes),
            schedule_lookback_days=s.nba_schedule_lookback_days,
            schedule_lookahead_days=s.nba_schedule_lookahead_days,
            lineup_tiers=parse_lineup_tiers(s.nba_lineup_poll_tiers),
            stale_run_after=timedelta(minutes=s.nba_live_cycle_stale_after_minutes),
        )

    @property
    def lineup_horizon(self) -> timedelta:
        """How long before tip-off lineup polling begins."""
        return self.lineup_tiers[0][0]

    def lineup_interval(self, time_to_tip: timedelta) -> timedelta | None:
        """How often a game this far from tip-off should be polled; None
        means not at all (too far out, or already tipped off)."""
        if time_to_tip <= timedelta(0) or time_to_tip > self.lineup_horizon:
            return None
        interval = None
        for bound, tier_interval in self.lineup_tiers:  # largest bound first
            if time_to_tip <= bound:
                interval = tier_interval
        return interval


# --- helpers ---------------------------------------------------------------


def _last_poll(db: Session, kind: str, scope: str | None = None) -> datetime | None:
    stmt = select(func.max(NbaEvidencePoll.observed_at)).where(NbaEvidencePoll.kind == kind)
    if scope is not None:
        stmt = stmt.where(NbaEvidencePoll.scope == scope)
    latest = db.scalar(stmt)
    return ensure_utc(latest) if latest is not None else None


def _due(last: datetime | None, interval: timedelta, now: datetime, force: bool) -> bool:
    return force or last is None or now - last >= interval


def _current_season_start_year(now: datetime) -> int:
    """The season a date falls in, for the depth-chart URL only: seasons
    start in the autumn, so July onward belongs to the season starting that
    year. (Game season membership always comes from the provider.)"""
    return now.year if now.month >= 7 else now.year - 1


class _Skip(Exception):
    pass


class _SourceFailure(Exception):
    """A step's requests ALL failed for source reasons."""


# --- steps -----------------------------------------------------------------


def _step_schedule(db: Session, stats: NbaStatsProvider, source: str, policy: NbaPollingPolicy, now: datetime, force: bool) -> str:
    if not _due(_last_poll(db, POLL_SCHEDULE), policy.schedule_interval, now, force):
        raise _Skip("not due")
    if not teams_by_source_id(db, source):
        sync_teams(db, stats)
    start = now.date() - timedelta(days=policy.schedule_lookback_days)
    end = now.date() + timedelta(days=policy.schedule_lookahead_days)
    report = sync_schedule(db, stats, start, end, source=source, today=now.date(), now=now)
    if report.dates_failed and len(report.dates_failed) == report.dates_requested:
        raise _SourceFailure(f"all {report.dates_requested} schedule dates failed: {report.dates_failed[0]}")
    db.add(
        NbaEvidencePoll(
            kind=POLL_SCHEDULE, scope=f"{start.isoformat()}..{end.isoformat()}", source=source, observed_at=now, items_seen=report.games.seen,
            observations_added=report.games.schedule_observations_added,
        )
    )
    db.commit()
    g = report.games
    detail = f"{report.dates_requested} dates; games seen {g.seen}, new {g.created}, changed {g.updated}; schedule observations added {g.schedule_observations_added}"
    return detail + (f"; {len(report.dates_failed)} date(s) failed, e.g. {report.dates_failed[0]}" if report.dates_failed else "")


def _step_availability(db: Session, evidence: NbaEvidenceProvider, policy: NbaPollingPolicy, now: datetime, force: bool) -> str:
    if not _due(_last_poll(db, POLL_AVAILABILITY), policy.availability_interval, now, force):
        raise _Skip("not due")
    feed = evidence.get_injuries()
    report = record_availability(db, feed)
    return (
        f"{report.items_seen} entries from {feed.teams_listed} teams; new {report.newly_listed}, changed {report.changed}, unchanged {report.unchanged}, "
        f"no longer listed {report.delisted}; unresolvable {report.unresolvable_items}; statuses {report.statuses}"
    )


def _per_team(db: Session, source: str, label: str, record: Callable[[str], bool]) -> str:
    teams = teams_by_source_id(db, source)
    if not teams:
        raise RuntimeError("no NBA teams stored")
    changed = 0
    source_failures: list[str] = []
    for source_team_id in sorted(teams, key=int):
        try:
            changed += int(record(source_team_id))
        except SOURCE_ERRORS as exc:
            db.rollback()
            source_failures.append(f"{source_team_id}: {str(exc)[:80]}")
    if len(source_failures) == len(teams):
        raise _SourceFailure(f"every {label} request failed, e.g. {source_failures[0]}")
    detail = f"{len(teams)} teams; new {label} observations {changed}"
    return detail + (f"; {len(source_failures)} request(s) failed, e.g. {source_failures[0]}" if source_failures else "")


def _step_team_rosters(db: Session, evidence: NbaEvidenceProvider, source: str, policy: NbaPollingPolicy, now: datetime, force: bool) -> str:
    if not _due(_last_poll(db, POLL_TEAM_ROSTER), policy.roster_interval, now, force):
        raise _Skip("not due")
    return _per_team(db, source, "roster", lambda tid: record_team_roster(db, evidence.get_team_roster(tid)))


def _step_team_depth_charts(db: Session, evidence: NbaEvidenceProvider, source: str, policy: NbaPollingPolicy, now: datetime, force: bool) -> str:
    if not _due(_last_poll(db, POLL_TEAM_DEPTH_CHART), policy.depth_chart_interval, now, force):
        raise _Skip("not due")
    season = _current_season_start_year(now)
    return _per_team(db, source, "depth chart", lambda tid: record_team_depth_chart(db, evidence.get_team_depth_chart(tid, season)))


def games_needing_lineup_poll(db: Session, source: str, policy: NbaPollingPolicy, now: datetime, force: bool = False) -> list[tuple[NbaGame, timedelta]]:
    """Scheduled games inside the lineup horizon whose tier interval has
    passed since their last lineup poll, with that interval."""
    games = db.scalars(
        select(NbaGame)
        .where(
            NbaGame.source == source,
            NbaGame.status == NbaGameStatus.SCHEDULED.value,
            NbaGame.scheduled_start > now,
            NbaGame.scheduled_start <= now + policy.lineup_horizon,
        )
        .order_by(NbaGame.scheduled_start, NbaGame.id)
    ).all()
    source_id_by_team = {team.id: source_id for source_id, team in teams_by_source_id(db, source).items()}
    due: list[tuple[NbaGame, timedelta]] = []
    for game in games:
        interval = policy.lineup_interval(ensure_utc(game.scheduled_start) - now)
        if interval is None:
            continue
        team_ids = [source_id_by_team.get(t) for t in (game.home_team_id, game.away_team_id)]
        if None in team_ids:
            continue
        last = [_last_poll(db, POLL_GAME_LINEUP, lineup_scope(game.source_game_id, tid)) for tid in team_ids]
        oldest = None if None in last else min(last)
        if _due(oldest, interval, now, force):
            due.append((game, interval))
    return due


def _step_game_lineups(db: Session, evidence: NbaEvidenceProvider, source: str, policy: NbaPollingPolicy, now: datetime, force: bool) -> str:
    due = games_needing_lineup_poll(db, source, policy, now, force)
    if not due:
        raise _Skip("no game is due a lineup poll")
    source_id_by_team = {team.id: source_id for source_id, team in teams_by_source_id(db, source).items()}
    polled = added = 0
    source_failures: list[str] = []
    for game, interval in due:
        team_ids = [source_id_by_team[game.home_team_id], source_id_by_team[game.away_team_id]]
        try:
            lineups = evidence.get_game_lineups(game.source_game_id, team_ids)
        except SOURCE_ERRORS as exc:
            source_failures.append(f"{game.source_game_id}: {str(exc)[:80]}")
            continue
        for lineup in lineups:
            try:
                added += int(record_game_lineup(db, game, lineup))
                polled += 1
            except SOURCE_ERRORS as exc:
                db.rollback()
                source_failures.append(f"{game.source_game_id}/{lineup.source_team_id}: {str(exc)[:80]}")
    if source_failures and not polled:
        raise _SourceFailure(f"every lineup request failed, e.g. {source_failures[0]}")
    detail = f"{len(due)} game(s) due; team lineups polled {polled}, new observations {added}"
    return detail + (f"; {len(source_failures)} failed, e.g. {source_failures[0]}" if source_failures else "")


def _step_box_scores(db: Session, stats: NbaStatsProvider, source: str, policy: NbaPollingPolicy, now: datetime, force: bool) -> str:
    if not _due(_last_poll(db, POLL_BOX_SCORES), policy.box_score_interval, now, force):
        raise _Skip("not due")
    start = now.date() - timedelta(days=policy.schedule_lookback_days)
    report = sync_box_scores(db, stats, start, now.date(), source=source)
    if report.games_seen and len(report.games_failed) == report.games_seen:
        raise _SourceFailure(f"all {report.games_seen} box-score requests failed, e.g. {report.games_failed[0]}")
    db.add(NbaEvidencePoll(kind=POLL_BOX_SCORES, scope=f"{start.isoformat()}..{now.date().isoformat()}", source=source, observed_at=now, items_seen=report.games_seen, observations_added=report.games_ingested))
    db.commit()
    if report.games_seen == 0:
        return "no final games awaiting a box score"
    return (
        f"{report.games_seen} game(s): ingested {report.games_ingested}, rejected {len(report.games_rejected)}, not final {len(report.games_not_final)}, "
        f"failed {len(report.games_failed)}; logs created {report.logs_created}"
    )


# --- locking and run lifecycle ---------------------------------------------


def close_dead_runs(db: Session, now: datetime, stale_after: timedelta, *, all_open: bool = False) -> int:
    """Mark runs left open by a process that died. With `all_open`, every
    open run is closed - correct only while holding the cycle lock, which
    proves no other cycle is alive. Steps those runs completed were
    committed as they went and stay valid."""
    stmt = select(NbaLiveCycleRun).where(NbaLiveCycleRun.status == RUN_IN_PROGRESS)
    if not all_open:
        stmt = stmt.where(NbaLiveCycleRun.started_at <= now - stale_after)
    dead = db.scalars(stmt).all()
    for run in dead:
        run.status = RUN_INTERRUPTED
    db.commit()
    return len(dead)


@contextmanager
def cycle_lock(db: Session, now: datetime, policy: NbaPollingPolicy) -> Iterator[None]:
    """Hold the one-cycle-at-a-time lock for the duration of a run, or raise
    CycleAlreadyRunning."""
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        # A dedicated connection, held for the whole run: a session-level
        # advisory lock belongs to the connection that took it, and the
        # ORM session may use different pooled connections between commits.
        # If this process dies, the database drops the connection and the
        # lock with it - no stale lock can outlive a crash.
        connection = bind.connect()
        try:
            acquired = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}).scalar()
            # A session-level lock outlives the transaction; ending it here
            # keeps this connection from sitting idle in a transaction.
            connection.commit()
            if not acquired:
                raise CycleAlreadyRunning("another NBA live cycle holds the database lock")
            try:
                close_dead_runs(db, now, policy.stale_run_after, all_open=True)
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
                connection.commit()
        finally:
            connection.close()
        return
    # Without advisory locks: an open run younger than the stale threshold
    # means another cycle is (or very recently was) alive.
    close_dead_runs(db, now, policy.stale_run_after)
    if db.scalar(select(func.count()).select_from(NbaLiveCycleRun).where(NbaLiveCycleRun.status == RUN_IN_PROGRESS)):
        raise CycleAlreadyRunning("another NBA live cycle is marked in progress")
    yield


def run_live_cycle(
    db: Session,
    stats: NbaStatsProvider,
    evidence: NbaEvidenceProvider,
    *,
    source: str,
    policy: NbaPollingPolicy | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> NbaLiveCycleRun:
    """Run every due step once. Raises CycleAlreadyRunning (having written
    nothing) if another cycle is running."""
    now = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    policy = policy or NbaPollingPolicy.from_settings()
    with cycle_lock(db, now, policy):
        run = NbaLiveCycleRun(started_at=now, status=RUN_IN_PROGRESS, steps=[])
        db.add(run)
        db.commit()

        steps: list[tuple[str, Callable[[], str]]] = [
            ("schedule", lambda: _step_schedule(db, stats, source, policy, now, force)),
            ("availability", lambda: _step_availability(db, evidence, policy, now, force)),
            ("team_rosters", lambda: _step_team_rosters(db, evidence, source, policy, now, force)),
            ("team_depth_charts", lambda: _step_team_depth_charts(db, evidence, source, policy, now, force)),
            ("game_lineups", lambda: _step_game_lineups(db, evidence, source, policy, now, force)),
            ("box_scores", lambda: _step_box_scores(db, stats, source, policy, now, force)),
        ]
        results: list[dict] = []
        for name, step in steps:
            began = time.monotonic()
            error_kind = None
            try:
                status, detail = STEP_OK, step()
            except _Skip as skip:
                status, detail = STEP_SKIPPED, str(skip)
            except (_SourceFailure, *SOURCE_ERRORS) as exc:
                db.rollback()
                status, detail, error_kind = STEP_FAILED, str(exc)[:300], ERROR_SOURCE
                logger.warning("nba.live_cycle.source_failure step=%s error=%s", name, str(exc)[:200])
            except Exception as exc:  # one failing step must not stop the others
                db.rollback()
                status, detail, error_kind = STEP_FAILED, f"{type(exc).__name__}: {str(exc)[:280]}", ERROR_INTERNAL
                logger.exception("nba.live_cycle.internal_failure step=%s", name)
            results.append({"step": name, "status": status, "error_kind": error_kind, "detail": detail, "seconds": round(time.monotonic() - began, 1)})
            run.steps = list(results)
            db.commit()

        run.status = run_status(results)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return run


def run_status(results: list[dict]) -> str:
    """ok: nothing failed. partial: only source failures, and at least one
    step that was due did succeed. failed: any internal error, or every step
    that was due failed."""
    failed = [r for r in results if r["status"] == STEP_FAILED]
    if not failed:
        return RUN_OK
    if any(r["error_kind"] == ERROR_INTERNAL for r in failed):
        return RUN_FAILED
    attempted = [r for r in results if r["status"] != STEP_SKIPPED]
    return RUN_FAILED if len(failed) == len(attempted) else RUN_PARTIAL
