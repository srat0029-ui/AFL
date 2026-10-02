"""The NBA live evidence cycle: one invocation refreshes whatever is due
and records what the source shows right now. Safe to run as often as you
like, by hand or on a schedule.

What a run does, in order:

1. schedule        refresh games in a window around today; a changed status
                   or tip-off time is appended as a schedule observation
2. availability    read the league injury feed and append what changed
3. team_evidence   each team's listed roster and depth chart
4. game_lineups    the listed lineup for each team in games near tip-off
5. box_scores      ingest box scores for games that have become final

It collects evidence only. No model runs, no prediction is made, no odds
are fetched and no API quota is spent.

Polling frequency
-----------------
Each step has a minimum interval (NbaPollingPolicy, read from settings). A
step whose last successful poll is more recent than its interval is skipped
as "not due", so the cycle's own cadence can be short without hammering the
source. Tightening a single interval - for example lineups near tip-off -
is a settings change. `force=True` ignores the intervals for one run.

Failure and interruption
------------------------
Every step is independent: one failing is recorded and the rest still run.
Every write inside a step is its own transaction (see app/nba/evidence.py
and ingestion.py), so a process killed mid-run leaves no partial record and
the next run simply does what is still due. The run row itself is committed
before the first step and closed at the end; a later run that finds one
still open marks it interrupted.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.prospective import ensure_utc
from app.models.nba import NbaEvidencePoll, NbaGame, NbaGameStatus, NbaLiveCycleRun
from app.models.nba.evidence import POLL_AVAILABILITY, POLL_GAME_LINEUP, POLL_SCHEDULE, POLL_TEAM_ROSTER, RUN_IN_PROGRESS, RUN_INTERRUPTED, RUN_OK, RUN_PARTIAL
from app.nba.evidence import lineup_scope, record_availability, record_game_lineup, record_team_depth_chart, record_team_roster
from app.nba.ingestion import NbaStatsProvider, sync_box_scores, sync_schedule, sync_teams, teams_by_source_id
from app.providers.nba.evidence_types import NbaDepthChart, NbaGameLineup, NbaInjuryFeed, NbaTeamRoster

logger = logging.getLogger(__name__)

STEP_OK = "ok"
STEP_SKIPPED = "skipped"
STEP_FAILED = "failed"

# A run still marked in progress after this long is taken to have died.
STALE_RUN_AFTER = timedelta(hours=2)


class NbaEvidenceProvider(Protocol):
    def get_injuries(self) -> NbaInjuryFeed: ...

    def get_team_roster(self, source_team_id: str) -> NbaTeamRoster: ...

    def get_team_depth_chart(self, source_team_id: str, season_start_year: int) -> NbaDepthChart: ...

    def get_game_lineup(self, source_game_id: str, source_team_id: str) -> NbaGameLineup: ...


@dataclass(frozen=True)
class NbaPollingPolicy:
    """How often each kind of evidence may be polled, and which games and
    dates are in scope. The single place polling behaviour is defined."""

    availability_interval: timedelta = timedelta(minutes=30)
    schedule_interval: timedelta = timedelta(hours=6)
    team_evidence_interval: timedelta = timedelta(hours=24)
    game_lineup_interval: timedelta = timedelta(minutes=15)
    schedule_lookback_days: int = 3
    schedule_lookahead_days: int = 14
    lineup_window_before: timedelta = timedelta(hours=6)
    lineup_window_after: timedelta = timedelta(hours=1)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "NbaPollingPolicy":
        s = settings or get_settings()
        return cls(
            availability_interval=timedelta(minutes=s.nba_poll_availability_minutes),
            schedule_interval=timedelta(minutes=s.nba_poll_schedule_minutes),
            team_evidence_interval=timedelta(minutes=s.nba_poll_team_rosters_minutes),
            game_lineup_interval=timedelta(minutes=s.nba_poll_game_lineup_minutes),
            schedule_lookback_days=s.nba_schedule_lookback_days,
            schedule_lookahead_days=s.nba_schedule_lookahead_days,
            lineup_window_before=timedelta(hours=s.nba_lineup_window_before_hours),
            lineup_window_after=timedelta(hours=s.nba_lineup_window_after_hours),
        )


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


def _step_schedule(db: Session, stats: NbaStatsProvider, source: str, policy: NbaPollingPolicy, now: datetime, force: bool) -> str:
    if not _due(_last_poll(db, POLL_SCHEDULE), policy.schedule_interval, now, force):
        raise _Skip("not due")
    if not teams_by_source_id(db, source):
        sync_teams(db, stats)
    start = now.date() - timedelta(days=policy.schedule_lookback_days)
    end = now.date() + timedelta(days=policy.schedule_lookahead_days)
    report = sync_schedule(db, stats, start, end, source=source, today=now.date(), now=now)
    if report.dates_failed and len(report.dates_failed) == report.dates_requested:
        raise RuntimeError(f"all {report.dates_requested} schedule dates failed: {report.dates_failed[0]}")
    db.add(NbaEvidencePoll(kind=POLL_SCHEDULE, scope=f"{start.isoformat()}..{end.isoformat()}", source=source, observed_at=now, items_seen=report.games.seen,
                           observations_added=report.games.schedule_observations_added))
    db.commit()
    g = report.games
    return (
        f"{report.dates_requested} dates ({len(report.dates_failed)} failed); games seen {g.seen}, new {g.created}, changed {g.updated}; "
        f"schedule observations added {g.schedule_observations_added}"
    )


def _step_availability(db: Session, evidence: NbaEvidenceProvider, policy: NbaPollingPolicy, now: datetime, force: bool) -> str:
    if not _due(_last_poll(db, POLL_AVAILABILITY), policy.availability_interval, now, force):
        raise _Skip("not due")
    feed = evidence.get_injuries()
    report = record_availability(db, feed)
    return (
        f"{report.items_seen} entries from {feed.teams_listed} teams; new {report.newly_listed}, changed {report.changed}, unchanged {report.unchanged}, "
        f"no longer listed {report.delisted}; unresolvable {report.unresolvable_items}; statuses {report.statuses}"
    )


def _step_team_evidence(db: Session, evidence: NbaEvidenceProvider, source: str, policy: NbaPollingPolicy, now: datetime, force: bool) -> str:
    if not _due(_last_poll(db, POLL_TEAM_ROSTER), policy.team_evidence_interval, now, force):
        raise _Skip("not due")
    teams = teams_by_source_id(db, source)
    if not teams:
        raise RuntimeError("no NBA teams stored")
    season = _current_season_start_year(now)
    rosters_changed = charts_changed = 0
    failures: list[str] = []
    for source_team_id in sorted(teams, key=int):
        for label, call in (
            ("roster", lambda tid=source_team_id: record_team_roster(db, evidence.get_team_roster(tid))),
            ("depth chart", lambda tid=source_team_id: record_team_depth_chart(db, evidence.get_team_depth_chart(tid, season))),
        ):
            try:
                changed = call()
            except Exception as exc:
                db.rollback()
                failures.append(f"{label} {source_team_id}: {str(exc)[:80]}")
                continue
            if label == "roster":
                rosters_changed += int(changed)
            else:
                charts_changed += int(changed)
    if len(failures) == 2 * len(teams):
        raise RuntimeError(f"every team request failed: {failures[0]}")
    detail = f"{len(teams)} teams; new roster observations {rosters_changed}, new depth-chart observations {charts_changed}"
    return detail + (f"; {len(failures)} request(s) failed, e.g. {failures[0]}" if failures else "")


def _step_game_lineups(db: Session, evidence: NbaEvidenceProvider, source: str, policy: NbaPollingPolicy, now: datetime, force: bool) -> str:
    games = db.scalars(
        select(NbaGame)
        .where(
            NbaGame.source == source,
            NbaGame.status.in_((NbaGameStatus.SCHEDULED.value, NbaGameStatus.IN_PROGRESS.value, NbaGameStatus.FINAL.value)),
            NbaGame.scheduled_start <= now + policy.lineup_window_before,
            NbaGame.scheduled_start >= now - policy.lineup_window_after,
        )
        .order_by(NbaGame.scheduled_start, NbaGame.id)
    ).all()
    if not games:
        raise _Skip("no games within the lineup window")
    source_id_by_team = {team.id: source_id for source_id, team in teams_by_source_id(db, source).items()}
    polled = added = not_due = 0
    failures: list[str] = []
    for game in games:
        for team_id in (game.home_team_id, game.away_team_id):
            source_team_id = source_id_by_team.get(team_id)
            if source_team_id is None:
                continue
            if not _due(_last_poll(db, POLL_GAME_LINEUP, lineup_scope(game.source_game_id, source_team_id)), policy.game_lineup_interval, now, force):
                not_due += 1
                continue
            try:
                added += int(record_game_lineup(db, game, evidence.get_game_lineup(game.source_game_id, source_team_id)))
                polled += 1
            except Exception as exc:
                db.rollback()
                failures.append(f"{game.source_game_id}/{source_team_id}: {str(exc)[:80]}")
    if failures and not polled:
        raise RuntimeError(f"every lineup request failed: {failures[0]}")
    detail = f"{len(games)} game(s) in window; lineups polled {polled}, new observations {added}, not due {not_due}"
    return detail + (f"; {len(failures)} failed, e.g. {failures[0]}" if failures else "")


def _step_box_scores(db: Session, stats: NbaStatsProvider, source: str, policy: NbaPollingPolicy, now: datetime) -> str:
    start = now.date() - timedelta(days=policy.schedule_lookback_days)
    report = sync_box_scores(db, stats, start, now.date(), source=source)
    if report.games_seen == 0:
        raise _Skip("no final games awaiting a box score")
    return (
        f"{report.games_seen} game(s): ingested {report.games_ingested}, rejected {len(report.games_rejected)}, not final {len(report.games_not_final)}, "
        f"failed {len(report.games_failed)}; logs created {report.logs_created}"
    )


def close_stale_runs(db: Session, now: datetime) -> int:
    """Mark runs left open by a process that died. Their completed steps
    were committed as they went and stay valid."""
    stale = db.scalars(select(NbaLiveCycleRun).where(NbaLiveCycleRun.status == RUN_IN_PROGRESS, NbaLiveCycleRun.started_at <= now - STALE_RUN_AFTER)).all()
    for run in stale:
        run.status = RUN_INTERRUPTED
    db.commit()
    return len(stale)


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
    now = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    policy = policy or NbaPollingPolicy.from_settings()
    close_stale_runs(db, now)
    run = NbaLiveCycleRun(started_at=now, status=RUN_IN_PROGRESS, steps=[])
    db.add(run)
    db.commit()

    steps: list[tuple[str, Callable[[], str]]] = [
        ("schedule", lambda: _step_schedule(db, stats, source, policy, now, force)),
        ("availability", lambda: _step_availability(db, evidence, policy, now, force)),
        ("team_evidence", lambda: _step_team_evidence(db, evidence, source, policy, now, force)),
        ("game_lineups", lambda: _step_game_lineups(db, evidence, source, policy, now, force)),
        ("box_scores", lambda: _step_box_scores(db, stats, source, policy, now)),
    ]
    results: list[dict] = []
    for name, step in steps:
        began = time.monotonic()
        try:
            status, detail = STEP_OK, step()
        except _Skip as skip:
            status, detail = STEP_SKIPPED, str(skip)
        except Exception as exc:  # one failing step must not stop the others
            db.rollback()
            logger.warning("nba.live_cycle.step_failed step=%s error=%s", name, str(exc)[:200])
            status, detail = STEP_FAILED, str(exc)[:300]
        results.append({"step": name, "status": status, "detail": detail, "seconds": round(time.monotonic() - began, 1)})
        run.steps = list(results)
        db.commit()

    run.status = RUN_PARTIAL if any(r["status"] == STEP_FAILED for r in results) else RUN_OK
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    return run

