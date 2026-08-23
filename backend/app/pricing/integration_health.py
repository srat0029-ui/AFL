"""Integration health (B2B Demo + Integration Readiness stage) — a single,
consumer-facing snapshot of "is the data behind this API actually
current," built entirely from already-persisted state (LiveCycleRun step
history, promoted model runs, the next upcoming round). Computes nothing
new about pricing itself; this is operational status only.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GoalModelRun, LiveCycleRun, ModelRun, PlayerModelRun
from app.player_modelling.upcoming_features import load_next_upcoming_round

# How stale a signal can get before it's flagged - the live cycle is meant
# to run every few minutes to hours (see app/player_modelling/scheduler.py),
# so a gap this large means something has stopped, not just "waiting for
# the next tick."
FIXTURE_REFRESH_WARN_HOURS = 24.0
ODDS_REFRESH_WARN_HOURS = 6.0

# The last N cycle runs scanned for the most recent SUCCESSFUL occurrence
# of a given step - a single run can fail one step (e.g. odds refresh)
# while others still succeed, so "last successful refresh" isn't always
# the most recent run's own timestamp.
RUNS_SCANNED = 20


@dataclass(frozen=True)
class StaleWarning:
    category: str
    detail: str


@dataclass(frozen=True)
class IntegrationHealth:
    status: str  # "ok" | "degraded"
    generated_at: datetime
    last_fixture_refresh: datetime | None
    last_odds_refresh: datetime | None
    current_round: int | None
    current_season_year: int | None
    promoted_models: dict[str, str]
    stale_warnings: list[StaleWarning] = field(default_factory=list)


def _aware(dt: datetime) -> datetime:
    """SQLite doesn't preserve tzinfo across a round trip (see
    live_cycle.py's own _aware helper) - every timestamp this app stores
    is genuinely UTC, so a naive value read back just needs that made
    explicit again before it's safe to subtract from an aware "now"."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _last_successful_step_at(db: Session, step_name: str) -> datetime | None:
    runs = db.scalars(select(LiveCycleRun).order_by(LiveCycleRun.run_at.desc()).limit(RUNS_SCANNED)).all()
    for run in runs:
        for step in run.steps:
            if step.get("step") == step_name and step.get("status") == "success":
                return _aware(run.run_at)
    return None


def load_integration_health(db: Session) -> IntegrationHealth:
    now = datetime.now(timezone.utc)
    last_fixture_refresh = _last_successful_step_at(db, "refresh_fixtures")
    last_odds_refresh = _last_successful_step_at(db, "refresh_prop_odds")

    upcoming = load_next_upcoming_round(db)
    current_round = upcoming[0].round_number if upcoming else None
    current_season_year = upcoming[0].season_year if upcoming else None

    promoted: dict[str, str] = {}
    disposal_run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.market == "player_disposals", PlayerModelRun.is_promoted.is_(True)))
    if disposal_run:
        promoted["player_disposals"] = f"{disposal_run.model_name}@{disposal_run.run_at.isoformat()}"
    goal_run = db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
    if goal_run:
        promoted["player_goals"] = f"{goal_run.model_name}@{goal_run.run_at.isoformat()}"
    for team_model in ("elo", "poisson"):
        run = db.scalar(select(ModelRun).where(ModelRun.model_name == team_model))
        if run:
            promoted[f"team_{team_model}"] = f"{run.model_name}@{run.run_at.isoformat()}"

    warnings: list[StaleWarning] = []
    if last_fixture_refresh is None:
        warnings.append(StaleWarning("fixtures", "No successful fixture refresh recorded yet."))
    elif (now - last_fixture_refresh).total_seconds() / 3600 > FIXTURE_REFRESH_WARN_HOURS:
        warnings.append(StaleWarning("fixtures", f"Last successful fixture refresh was {(now - last_fixture_refresh).total_seconds() / 3600:.1f}h ago."))
    if last_odds_refresh is None:
        warnings.append(StaleWarning("odds", "No successful odds refresh recorded yet."))
    elif (now - last_odds_refresh).total_seconds() / 3600 > ODDS_REFRESH_WARN_HOURS:
        warnings.append(StaleWarning("odds", f"Last successful odds refresh was {(now - last_odds_refresh).total_seconds() / 3600:.1f}h ago."))
    if current_round is None:
        warnings.append(StaleWarning("schedule", "No upcoming round identified — nothing currently scheduled/loaded."))
    for market in ("player_disposals", "player_goals", "team_elo", "team_poisson"):
        if market not in promoted:
            warnings.append(StaleWarning("model", f"No promoted model found for {market}."))

    status = "ok" if not warnings else "degraded"
    return IntegrationHealth(
        status=status, generated_at=now, last_fixture_refresh=last_fixture_refresh, last_odds_refresh=last_odds_refresh,
        current_round=current_round, current_season_year=current_season_year, promoted_models=promoted, stale_warnings=warnings,
    )
