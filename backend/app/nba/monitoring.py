"""Is the NBA evidence collector still running? Read-only.

Prospective evidence can only be collected at the time; a collector that
silently stops leaves a gap that can never be filled. This module answers,
from the database alone, how recently each kind of evidence was last
successfully collected and whether that is later than it should be.

A kind of evidence is STALE when its last successful poll is older than
its polling interval times a multiplier (default 3, from settings). So with
a 30-minute injury interval, the injury feed is flagged after 90 minutes
without a successful read. A kind is only judged when it is actually
expected:

- lineups only while some scheduled game is inside the lineup horizon;
- box scores only while some game inside the schedule look-back window has
  become final;
- everything only once the collector has run at least once.

Nothing here makes a request to any source.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.prospective import ensure_utc
from app.models.nba import NbaEvidencePoll, NbaGame, NbaGameStatus, NbaLiveCycleRun, NbaPlayerAvailabilityReport
from app.models.nba.evidence import (
    POLL_AVAILABILITY,
    POLL_BOX_SCORES,
    POLL_GAME_LINEUP,
    POLL_SCHEDULE,
    POLL_TEAM_DEPTH_CHART,
    POLL_TEAM_ROSTER,
    RUN_IN_PROGRESS,
    RUN_OK,
)
from app.nba.live_cycle import NbaPollingPolicy


@dataclass(frozen=True)
class EvidenceCheck:
    kind: str
    label: str
    last_success_at: datetime | None
    age_minutes: float | None
    interval_minutes: float
    stale_after_minutes: float
    expected: bool  # whether this kind of evidence should currently be being collected
    stale: bool


@dataclass
class EvidenceHealth:
    checked_at: datetime
    healthy: bool
    latest_run_at: datetime | None
    latest_run_status: str | None
    latest_successful_run_at: datetime | None  # latest run that finished with status "ok"
    upcoming_games: int  # scheduled games not yet tipped off
    games_in_lineup_window: int
    current_availability_entries: int  # players the injury feed lists as of the latest observation
    checks: list[EvidenceCheck] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _latest_poll(db: Session, kind: str) -> datetime | None:
    latest = db.scalar(select(func.max(NbaEvidencePoll.observed_at)).where(NbaEvidencePoll.kind == kind))
    return ensure_utc(latest) if latest is not None else None


def current_availability_entries(db: Session) -> int:
    """Players whose most recent injury-feed observation has them listed."""
    latest_ids = select(func.max(NbaPlayerAvailabilityReport.id)).group_by(NbaPlayerAvailabilityReport.player_id)
    return db.scalar(
        select(func.count()).select_from(NbaPlayerAvailabilityReport).where(NbaPlayerAvailabilityReport.id.in_(latest_ids), NbaPlayerAvailabilityReport.is_listed.is_(True))
    ) or 0


def evidence_health(db: Session, *, now: datetime | None = None, policy: NbaPollingPolicy | None = None, settings: Settings | None = None) -> EvidenceHealth:
    now = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    settings = settings or get_settings()
    policy = policy or NbaPollingPolicy.from_settings(settings)
    multiplier = settings.nba_monitor_stale_multiplier

    latest_run = db.scalar(select(NbaLiveCycleRun).order_by(NbaLiveCycleRun.started_at.desc(), NbaLiveCycleRun.id.desc()).limit(1))
    latest_ok = db.scalar(select(func.max(NbaLiveCycleRun.started_at)).where(NbaLiveCycleRun.status == RUN_OK))
    upcoming = db.scalar(select(func.count()).select_from(NbaGame).where(NbaGame.status == NbaGameStatus.SCHEDULED.value, NbaGame.scheduled_start > now)) or 0
    in_window = db.scalar(
        select(func.count())
        .select_from(NbaGame)
        .where(NbaGame.status == NbaGameStatus.SCHEDULED.value, NbaGame.scheduled_start > now, NbaGame.scheduled_start <= now + policy.lineup_horizon)
    ) or 0
    recent_final = db.scalar(
        select(NbaGame.id)
        .where(NbaGame.status == NbaGameStatus.FINAL.value, NbaGame.scheduled_start >= now - timedelta(days=policy.schedule_lookback_days), NbaGame.scheduled_start <= now)
        .limit(1)
    ) is not None
    ever_ran = latest_run is not None

    plan = [
        (POLL_AVAILABILITY, "Injury / availability feed", policy.availability_interval, ever_ran),
        (POLL_SCHEDULE, "Schedule", policy.schedule_interval, ever_ran),
        (POLL_TEAM_ROSTER, "Team rosters", policy.roster_interval, ever_ran),
        (POLL_TEAM_DEPTH_CHART, "Depth charts", policy.depth_chart_interval, ever_ran),
        # Judged against the slowest tier: a game anywhere in the window is
        # due at least that often.
        (POLL_GAME_LINEUP, "Pregame lineups", max(interval for _, interval in policy.lineup_tiers), ever_ran and in_window > 0),
        (POLL_BOX_SCORES, "Box scores", policy.box_score_interval, ever_ran and recent_final),
    ]
    checks: list[EvidenceCheck] = []
    problems: list[str] = []
    for kind, label, interval, expected in plan:
        last = _latest_poll(db, kind)
        age = (now - last).total_seconds() / 60 if last is not None else None
        stale_after = interval.total_seconds() / 60 * multiplier
        stale = expected and (age is None or age > stale_after)
        checks.append(EvidenceCheck(kind, label, last, round(age, 1) if age is not None else None, interval.total_seconds() / 60, stale_after, expected, stale))
        if stale:
            problems.append(f"{label}: last successful poll {'never' if last is None else f'{age:.0f} minutes ago'} (stale after {stale_after:.0f})")

    if not ever_ran:
        problems.append("The live cycle has never run against this database.")
    elif latest_run.status == RUN_IN_PROGRESS and now - ensure_utc(latest_run.started_at) > policy.stale_run_after:
        problems.append(f"The latest run (started {ensure_utc(latest_run.started_at).isoformat()}) never finished.")

    return EvidenceHealth(
        checked_at=now,
        healthy=not problems,
        latest_run_at=ensure_utc(latest_run.started_at) if latest_run else None,
        latest_run_status=latest_run.status if latest_run else None,
        latest_successful_run_at=ensure_utc(latest_ok) if latest_ok else None,
        upcoming_games=upcoming,
        games_in_lineup_window=in_window,
        current_availability_entries=current_availability_entries(db),
        checks=checks,
        problems=problems,
    )
