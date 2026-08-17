"""Read-only operational status for the current upcoming AFL round —
Sections 5-6, 18 of the live-operations stage brief. Every function here
only ever reads already-persisted state; nothing triggers an external
request, paid or free (Section 18: "Do not trigger external paid API
requests just by opening this page" — this module is what makes that
guarantee possible for a status API endpoint).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    LiveCycleRun,
    Match,
    MatchStatus,
    PlayerDisposalProjection,
    PlayerGoalProjection,
    PlayerPropMarket,
    PropMarketObservation,
    Team,
)
from app.player_modelling.live_report_query import load_lineup_summary
from app.player_modelling.prop_market_diagnosis import DIAG_ODDS_AVAILABLE, MatchMarketDiagnosis, diagnose_match_market_coverage
from app.player_modelling.upcoming_features import UpcomingMatchTeams, load_next_upcoming_round

STATUS_WAITING_FOR_TEAMS = "waiting_for_teams"
STATUS_WAITING_FOR_BOOKMAKER_MARKETS = "waiting_for_bookmaker_markets"
STATUS_STALE = "stale"
STATUS_READY = "ready"
STATUS_ODDS_AVAILABLE = "odds_available"
STATUS_COMPLETED_AWAITING_STATS = "completed_awaiting_player_stats"
STATUS_SETTLED = "settled"
STATUS_COMPLETED_NO_MARKET_DATA = "completed_no_market_data"


@dataclass(frozen=True)
class MatchCoverageStatus:
    match_id: int
    home_team_name: str
    away_team_name: str
    scheduled_start: datetime
    match_status: str
    simple_status: str
    lineup_announcement_state: str
    projections_generated: bool
    bookmaker_event_exists: bool
    bookmaker_props_observed: bool
    bookmakers_observed: list[str]
    n_quotes: int
    last_odds_refresh: datetime | None
    n_observations: int
    n_observations_settled: int
    n_observations_awaiting_settlement: int
    diagnosis: MatchMarketDiagnosis


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _simple_status_for_match(
    match: Match, lineup_state: str, bookmaker_props_observed: bool, projections_generated: bool,
    diagnosis: MatchMarketDiagnosis, n_observations: int, n_settled: int, n_awaiting_stats: int,
) -> str:
    if match.status == MatchStatus.COMPLETED:
        if n_observations == 0:
            return STATUS_COMPLETED_NO_MARKET_DATA
        if n_awaiting_stats > 0:
            return STATUS_COMPLETED_AWAITING_STATS
        if n_settled == n_observations:
            return STATUS_SETTLED
        return STATUS_COMPLETED_AWAITING_STATS

    if lineup_state != "final_team_confirmed":
        return STATUS_WAITING_FOR_TEAMS
    if not bookmaker_props_observed:
        return STATUS_WAITING_FOR_BOOKMAKER_MARKETS
    if not diagnosis.would_be_skipped_this_cycle:
        # The match-time-aware refresh policy says this match's odds are
        # due for a refresh right now — the data we're showing predates
        # that, so it's stale rather than current.
        return STATUS_STALE
    if projections_generated:
        return STATUS_READY
    return STATUS_ODDS_AVAILABLE


def load_match_coverage(db: Session, match: UpcomingMatchTeams, *, expected_bookmaker_name: str | None = "SportsBet") -> MatchCoverageStatus:
    match_row = db.get(Match, match.match_id)
    lineup_summary = load_lineup_summary(db, match.match_id)

    projections_generated = (
        db.scalar(select(PlayerDisposalProjection.id).where(PlayerDisposalProjection.match_id == match.match_id).limit(1)) is not None
        or db.scalar(select(PlayerGoalProjection.id).where(PlayerGoalProjection.match_id == match.match_id).limit(1)) is not None
    )

    quotes = db.scalars(select(PlayerPropMarket).where(PlayerPropMarket.match_id == match.match_id, PlayerPropMarket.source != "manual")).all()
    bookmakers_observed = sorted({q.bookmaker.name for q in quotes})
    last_refresh = max((q.recorded_at for q in quotes), default=None)

    observations = db.scalars(select(PropMarketObservation).where(PropMarketObservation.match_id == match.match_id)).all()
    n_settled = sum(1 for o in observations if o.settled_at is not None)

    diagnosis = diagnose_match_market_coverage(db, match_row, expected_bookmaker_name=expected_bookmaker_name)

    # "Awaiting settlement" here is a display-only estimate (matches Section
    # 14's "awaiting player stats" case) - a genuinely fresh assessment
    # requires re-running settle_observation, which this read-only status
    # view deliberately never does; it's approximated as "unsettled AND the
    # match has completed" so the dashboard can show it without side effects.
    n_awaiting = sum(1 for o in observations if o.settled_at is None) if match_row.status == MatchStatus.COMPLETED else 0

    simple_status = _simple_status_for_match(
        match_row, lineup_summary["announcement_state"], bool(quotes), projections_generated, diagnosis, len(observations), n_settled, n_awaiting,
    )

    home = db.get(Team, match.home_team_id)
    away = db.get(Team, match.away_team_id)

    return MatchCoverageStatus(
        match_id=match.match_id, home_team_name=home.name, away_team_name=away.name, scheduled_start=match.scheduled_start,
        match_status=match_row.status.value, simple_status=simple_status,
        lineup_announcement_state=lineup_summary["announcement_state"], projections_generated=projections_generated,
        bookmaker_event_exists=bool((match_row.external_ids or {}).get("the_odds_api")), bookmaker_props_observed=bool(quotes),
        bookmakers_observed=bookmakers_observed, n_quotes=len(quotes), last_odds_refresh=last_refresh,
        n_observations=len(observations), n_observations_settled=n_settled, n_observations_awaiting_settlement=n_awaiting,
        diagnosis=diagnosis,
    )


@dataclass(frozen=True)
class RoundSummary:
    """Section 6's top-of-page numbers."""

    n_upcoming_matches: int
    n_matches_with_projections: int
    n_matches_with_bookmaker_events: int
    n_matches_with_prop_markets: int
    n_unique_players_with_markets: int
    n_real_quotes_stored: int
    n_real_observations_stored: int
    n_confirmed_lineups: int  # matches at final_team_confirmed
    n_placeholder_or_uncertain_lineups: int  # matches NOT yet at final_team_confirmed


@dataclass(frozen=True)
class LiveStatusReport:
    round_summary: RoundSummary
    matches: list[MatchCoverageStatus]
    recent_runs: list[LiveCycleRun]


def load_live_status(db: Session, *, recent_runs_limit: int = 5) -> LiveStatusReport:
    upcoming_matches = load_next_upcoming_round(db)
    match_coverages = [load_match_coverage(db, m) for m in upcoming_matches]

    total_quotes = sum(mc.n_quotes for mc in match_coverages)
    total_observations = sum(mc.n_observations for mc in match_coverages)
    unique_players = (
        len(set(db.scalars(select(PropMarketObservation.player_id).where(PropMarketObservation.match_id.in_(match_ids))).all()))
        if (match_ids := [m.match_id for m in upcoming_matches])
        else 0
    )

    summary = RoundSummary(
        n_upcoming_matches=len(upcoming_matches),
        n_matches_with_projections=sum(1 for mc in match_coverages if mc.projections_generated),
        n_matches_with_bookmaker_events=sum(1 for mc in match_coverages if mc.bookmaker_event_exists),
        n_matches_with_prop_markets=sum(1 for mc in match_coverages if mc.bookmaker_props_observed),
        n_unique_players_with_markets=unique_players,
        n_real_quotes_stored=total_quotes,
        n_real_observations_stored=total_observations,
        n_confirmed_lineups=sum(1 for mc in match_coverages if mc.lineup_announcement_state == "final_team_confirmed"),
        n_placeholder_or_uncertain_lineups=sum(1 for mc in match_coverages if mc.lineup_announcement_state != "final_team_confirmed"),
    )

    recent_runs = list(db.scalars(select(LiveCycleRun).order_by(LiveCycleRun.run_at.desc()).limit(recent_runs_limit)).all())

    return LiveStatusReport(round_summary=summary, matches=match_coverages, recent_runs=recent_runs)
