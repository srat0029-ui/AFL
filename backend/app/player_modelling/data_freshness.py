"""Read-only data-freshness summary for the compact freshness panel shown on
Dashboard and Weekly Review (product-polish stage). Aggregates already-
persisted timestamps across the six categories a normal weekly user cares
about — like live_status.py, this module only ever reads already-persisted
state and NEVER triggers an external request, so it's safe to call on every
page load.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Match, OddsQuote, PlayerDisposalProjection, PlayerGoalProjection, PlayerPropMarket, VenueWeatherSnapshot
from app.player_modelling.live_report_query import load_lineup_summary
from app.player_modelling.prop_odds_quota import recommended_refresh_interval
from app.player_modelling.team_odds_ingestion import PROVIDER_NAME as TEAM_ODDS_PROVIDER_NAME
from app.player_modelling.team_selection_ingestion import ANNOUNCEMENT_FINAL_CONFIRMED, ANNOUNCEMENT_NOT_ANNOUNCED
from app.player_modelling.upcoming_features import load_next_upcoming_round

FRESH = "fresh"
AGING = "aging"
STALE = "stale"
NOT_AVAILABLE = "not_available"

WAITING_FOR_BOOKMAKER_MARKETS = "Waiting for bookmaker markets."

# Fixed, disclosed thresholds for the categories with no bookmaker-specific
# refresh policy of their own (mirrors weather_ingestion.py's own severe-
# weather constants — a plain-language flag on top of raw data, not tuned
# against outcomes).
FIXTURES_AGING_AFTER = timedelta(hours=12)
FIXTURES_STALE_AFTER = timedelta(hours=48)
WEATHER_AGING_AFTER = timedelta(hours=12)
WEATHER_STALE_AFTER = timedelta(hours=24)
PROJECTIONS_AGING_AFTER = timedelta(hours=12)
PROJECTIONS_STALE_AFTER = timedelta(hours=48)

# How much staler than the match-time-aware odds interval (see
# prop_odds_quota.py) counts as "stale" rather than merely "aging" for the
# two bookmaker-price categories.
ODDS_STALE_MULTIPLIER = 3


@dataclass(frozen=True)
class FreshnessItem:
    category: str
    label: str
    status: str  # fresh | aging | stale | not_available
    last_refreshed: datetime | None
    detail: str


@dataclass(frozen=True)
class DataFreshnessReport:
    items: list[FreshnessItem]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _bucket(last: datetime | None, aging_after: timedelta, stale_after: timedelta) -> str:
    if last is None:
        return NOT_AVAILABLE
    age = datetime.now(timezone.utc) - _aware(last)
    if age < aging_after:
        return FRESH
    if age < stale_after:
        return AGING
    return STALE


def _time_item(category: str, label: str, last: datetime | None, aging_after: timedelta, stale_after: timedelta, detail: str, not_available_detail: str) -> FreshnessItem:
    status = _bucket(last, aging_after, stale_after)
    return FreshnessItem(category=category, label=label, status=status, last_refreshed=last, detail=detail if status != NOT_AVAILABLE else not_available_detail)


def load_data_freshness(db: Session) -> DataFreshnessReport:
    upcoming = load_next_upcoming_round(db)
    if not upcoming:
        no_matches = "No upcoming AFL fixtures found."
        return DataFreshnessReport(
            items=[
                FreshnessItem("fixtures", "Fixtures", NOT_AVAILABLE, None, no_matches),
                FreshnessItem("team_odds", "Team odds", NOT_AVAILABLE, None, no_matches),
                FreshnessItem("player_props", "Player props", NOT_AVAILABLE, None, no_matches),
                FreshnessItem("weather", "Weather", NOT_AVAILABLE, None, no_matches),
                FreshnessItem("lineup_status", "Team/lineup status", NOT_AVAILABLE, None, no_matches),
                FreshnessItem("projections", "Projections", NOT_AVAILABLE, None, no_matches),
            ]
        )

    match_ids = [m.match_id for m in upcoming]
    now = datetime.now(timezone.utc)
    soonest_hours = min((_aware(m.scheduled_start) - now).total_seconds() / 3600.0 for m in upcoming)
    odds_interval = recommended_refresh_interval(soonest_hours)
    odds_stale_after = odds_interval * ODDS_STALE_MULTIPLIER

    items: list[FreshnessItem] = []

    last_fixture = db.scalar(select(func.max(Match.updated_at)).where(Match.id.in_(match_ids)))
    items.append(_time_item(
        "fixtures", "Fixtures", last_fixture, FIXTURES_AGING_AFTER, FIXTURES_STALE_AFTER,
        "Upcoming match details as last ingested.", "No fixture data recorded yet.",
    ))

    last_team_odds = db.scalar(
        select(func.max(OddsQuote.recorded_at)).where(OddsQuote.match_id.in_(match_ids), OddsQuote.source == TEAM_ODDS_PROVIDER_NAME)
    )
    items.append(_time_item(
        "team_odds", "Team odds", last_team_odds, odds_interval, odds_stale_after,
        "Automated h2h/line/total match prices.", WAITING_FOR_BOOKMAKER_MARKETS,
    ))

    last_props = db.scalar(
        select(func.max(PlayerPropMarket.recorded_at)).where(PlayerPropMarket.match_id.in_(match_ids), PlayerPropMarket.source != "manual")
    )
    items.append(_time_item(
        "player_props", "Player props", last_props, odds_interval, odds_stale_after,
        "Automated bookmaker player prop prices.", WAITING_FOR_BOOKMAKER_MARKETS,
    ))

    last_weather = db.scalar(select(func.max(VenueWeatherSnapshot.fetched_at)).where(VenueWeatherSnapshot.match_id.in_(match_ids)))
    items.append(_time_item(
        "weather", "Weather", last_weather, WEATHER_AGING_AFTER, WEATHER_STALE_AFTER,
        "Venue-local kickoff forecast.", "No forecast collected yet for these venues.",
    ))

    summaries = [load_lineup_summary(db, mid) for mid in match_ids]
    states = [s["announcement_state"] for s in summaries]
    n_confirmed = sum(1 for s in states if s == ANNOUNCEMENT_FINAL_CONFIRMED)
    if n_confirmed == 0:
        # A bulk-loaded placeholder roster is not the same claim as "nothing
        # entered yet" - a placeholder must never read as confirmed, but the
        # message should still say a roster exists so it isn't mistaken for
        # a genuinely empty state.
        n_with_placeholder_roster = sum(1 for s in summaries if s["announcement_state"] == ANNOUNCEMENT_NOT_ANNOUNCED and s["n_placeholder"] > 0)
        detail = (
            f"Roster loaded for {n_with_placeholder_roster} match(es) — teams not confirmed."
            if n_with_placeholder_roster > 0
            else "Teams not yet announced."
        )
        items.append(FreshnessItem("lineup_status", "Team/lineup status", NOT_AVAILABLE, None, detail))
    elif n_confirmed == len(states):
        items.append(FreshnessItem("lineup_status", "Team/lineup status", FRESH, None, "All upcoming matches have confirmed teams."))
    else:
        items.append(FreshnessItem(
            "lineup_status", "Team/lineup status", AGING, None,
            f"{n_confirmed} of {len(states)} match(es) have confirmed teams — others not yet announced.",
        ))

    last_disposal_proj = db.scalar(select(func.max(PlayerDisposalProjection.updated_at)).where(PlayerDisposalProjection.match_id.in_(match_ids)))
    last_goal_proj = db.scalar(select(func.max(PlayerGoalProjection.updated_at)).where(PlayerGoalProjection.match_id.in_(match_ids)))
    candidates = [t for t in (last_disposal_proj, last_goal_proj) if t is not None]
    last_projection = max(candidates) if candidates else None
    items.append(_time_item(
        "projections", "Projections", last_projection, PROJECTIONS_AGING_AFTER, PROJECTIONS_STALE_AFTER,
        "Live disposal/goal projections for the upcoming round.", "No live projections generated yet.",
    ))

    return DataFreshnessReport(items=items)
