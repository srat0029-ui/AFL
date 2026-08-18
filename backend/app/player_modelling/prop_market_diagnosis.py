"""Diagnoses WHY an upcoming match currently has no (or incomplete) prop
market data — Section 12 of the live-operations stage brief: "Do not
collapse all of these into 'No odds.'" A blank Prop Insights table is
consistent with at least five different real causes, each requiring a
different response from a human operator (nothing to do yet vs. the
provider genuinely doesn't cover this match vs. quota is throttling
refreshes vs. only one side of the market is covered) — reporting them
together as one undifferentiated "no odds" status would hide which one is
actually true.

Two operating modes, both read-only (this module never fetches anything
itself):

- `provider_events` supplied: used right after a genuinely fresh (free —
  see prop_odds_quota.py) `provider.list_events()` call, e.g. from within
  a live cycle. Can distinguish "provider genuinely has no event for this
  match" from "we just haven't checked."
- `provider_events=None`: persisted-state-only mode, safe to call from a
  read-only status endpoint that must never trigger an external request
  (Section 18: "Do not trigger external paid API requests just by opening
  this page"). Degrades honestly to "not_yet_refreshed" wherever fresh
  event data would have been needed to tell two causes apart, rather than
  guessing.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, PlayerPropMarket
from app.player_modelling.market import PlayerMarket
from app.player_modelling.prop_odds_matching import resolve_event_to_match
from app.player_modelling.prop_odds_quota import event_needs_refresh, recommended_refresh_interval
from app.providers.types import ProviderEvent

DIAG_EVENT_ABSENT = "event_absent"  # provider was actually checked and has no event for this match
DIAG_NOT_YET_REFRESHED = "not_yet_refreshed"  # no fresh check has ever resolved (or failed to resolve) an event for this match
DIAG_EVENT_NO_PROPS = "event_no_props"  # event resolved at least once, but zero automated quotes of any market were ever returned
DIAG_DISPOSAL_MARKET_ABSENT = "disposal_market_absent"  # quotes exist, but none are player_disposals
DIAG_BOOKMAKER_ABSENT = "bookmaker_absent"  # disposal quotes exist, but not from the expected/preferred bookmaker
DIAG_ODDS_AVAILABLE = "odds_available"


@dataclass(frozen=True)
class MatchMarketDiagnosis:
    match_id: int
    category: str
    detail: str
    would_be_skipped_this_cycle: bool  # True if the match-time-aware quota policy would currently skip this match
    hours_to_kickoff: float
    disposals_available: bool = False  # at least one real player_disposals quote exists for this match, any bookmaker
    goals_available: bool = False  # at least one real player_goals quote exists for this match, any bookmaker
    unique_player_count: int = 0  # distinct players with at least one real quote (either market), any bookmaker


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def diagnose_match_market_coverage(
    db: Session,
    match: Match,
    *,
    provider: str = "the_odds_api",
    provider_events: list[ProviderEvent] | None = None,
    expected_bookmaker_name: str | None = "SportsBet",
    min_refresh_interval: timedelta | None = None,
) -> MatchMarketDiagnosis:
    now = datetime.now(timezone.utc)
    hours_to_kickoff = (_aware(match.scheduled_start) - now).total_seconds() / 3600.0
    effective_interval = min_refresh_interval if min_refresh_interval is not None else recommended_refresh_interval(hours_to_kickoff)
    would_skip = not event_needs_refresh(db, match.id, provider, effective_interval)

    event_ever_resolved = bool((match.external_ids or {}).get(provider))

    if provider_events is not None:
        resolved_match_ids: set[int] = set()
        for e in provider_events:
            resolved = resolve_event_to_match(db, e).match
            if resolved is not None:
                resolved_match_ids.add(resolved.id)
        event_resolved_now = match.id in resolved_match_ids
        if not event_resolved_now and not event_ever_resolved:
            return MatchMarketDiagnosis(
                match_id=match.id, category=DIAG_EVENT_ABSENT,
                detail=f"{provider} currently lists no event matching this fixture.",
                would_be_skipped_this_cycle=would_skip, hours_to_kickoff=hours_to_kickoff,
            )
    elif not event_ever_resolved:
        return MatchMarketDiagnosis(
            match_id=match.id, category=DIAG_NOT_YET_REFRESHED,
            detail=f"No {provider} event has ever been resolved to this match — a refresh hasn't happened, or hasn't succeeded, yet.",
            would_be_skipped_this_cycle=would_skip, hours_to_kickoff=hours_to_kickoff,
        )

    quotes = db.scalars(select(PlayerPropMarket).where(PlayerPropMarket.match_id == match.id, PlayerPropMarket.source == provider)).all()
    if not quotes:
        return MatchMarketDiagnosis(
            match_id=match.id, category=DIAG_EVENT_NO_PROPS,
            detail=f"The event resolved, but {provider} has returned zero quotes for any market so far.",
            would_be_skipped_this_cycle=would_skip, hours_to_kickoff=hours_to_kickoff,
        )

    disposal_quotes = [q for q in quotes if q.market_type == PlayerMarket.DISPOSALS.value]
    goal_quotes = [q for q in quotes if q.market_type == PlayerMarket.GOALS.value]
    unique_player_count = len({q.player_id for q in quotes})

    if not disposal_quotes:
        markets_seen = sorted({q.market_type for q in quotes})
        return MatchMarketDiagnosis(
            match_id=match.id, category=DIAG_DISPOSAL_MARKET_ABSENT,
            detail=f"Quotes exist ({', '.join(markets_seen)}) but none are player_disposals.",
            would_be_skipped_this_cycle=would_skip, hours_to_kickoff=hours_to_kickoff,
            disposals_available=False, goals_available=bool(goal_quotes), unique_player_count=unique_player_count,
        )

    if expected_bookmaker_name is not None:
        bookmaker_names = {q.bookmaker.name for q in disposal_quotes}
        if expected_bookmaker_name not in bookmaker_names:
            return MatchMarketDiagnosis(
                match_id=match.id, category=DIAG_BOOKMAKER_ABSENT,
                detail=f"Disposal quotes exist from {sorted(bookmaker_names)}, but not from {expected_bookmaker_name!r}.",
                would_be_skipped_this_cycle=would_skip, hours_to_kickoff=hours_to_kickoff,
                disposals_available=True, goals_available=bool(goal_quotes), unique_player_count=unique_player_count,
            )

    return MatchMarketDiagnosis(
        match_id=match.id, category=DIAG_ODDS_AVAILABLE,
        detail=f"{len(disposal_quotes)} disposal quote(s) available.",
        would_be_skipped_this_cycle=would_skip, hours_to_kickoff=hours_to_kickoff,
        disposals_available=True, goals_available=bool(goal_quotes), unique_player_count=unique_player_count,
    )
