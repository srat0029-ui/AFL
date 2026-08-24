"""Context staleness (item 4): a bookmaker's latest quote for a market may
predate a lineup or team-news event that could be relevant to that exact
market. This is deliberately NOT a claim that the price needs to move —
sportsbooks reprice on their own schedule for reasons this engine has no
visibility into (liquidity, trader review cadence, risk limits). It is
purely a surfaced fact: "this event happened; this quote is older than it."

Two independent sources of "context," both already-existing, unmodified
infrastructure:
  - ExpectedLineup (one current row per player-match — ingestion services
    already overwrite it in place, so `recorded_at`/`source_timestamp` is
    "when the CURRENT state became known," which is what matters here).
    A "notable" lineup event is one of the states in NOTABLE_SELECTION_STATUSES
    below — a plain "still placeholder" row is not itself an event.
  - MatchContextItem (current_context_for_match — team news, injuries,
    substitute changes; see match_context_service.py).

Reason code is always MARKET_MAY_PREDATE_CONTEXT (item 4's own wording),
regardless of which alert_type (STALE_AFTER_LINEUP_CHANGE vs
STALE_AFTER_CONTEXT_CHANGE) it's attached to.
"""

from dataclasses import dataclass
from datetime import datetime

from app.models import CONTEXT_TYPE_LABELS, ExpectedLineup, SelectionStatus
from app.market_monitor.common import aware

REASON_CODE = "MARKET_MAY_PREDATE_CONTEXT"

# A lineup row moving into any of these states is a genuine event a
# trading desk would care about; NAMED_IN_SQUAD/PLACEHOLDER churn is
# routine roster noise, not the kind of thing item 4's examples describe.
NOTABLE_SELECTION_STATUSES: frozenset[str] = frozenset(
    {
        SelectionStatus.CONFIRMED_OUT.value,
        SelectionStatus.SUBSTITUTE.value,
        SelectionStatus.EMERGENCY.value,
        SelectionStatus.UNCERTAIN.value,
        SelectionStatus.CONFIRMED_SELECTED.value,  # "team announcement" (item 4's own example)
    }
)


@dataclass(frozen=True)
class ContextStalenessResult:
    is_stale: bool
    context_event_at: datetime | None
    context_description: str | None


def _lineup_event_timestamp(lineup: ExpectedLineup) -> datetime | None:
    if lineup.selection_status not in NOTABLE_SELECTION_STATUSES:
        return None
    return aware(lineup.source_timestamp if lineup.source_timestamp is not None else lineup.recorded_at)


def check_lineup_staleness(lineup: ExpectedLineup | None, latest_quote_at: datetime) -> ContextStalenessResult:
    if lineup is None:
        return ContextStalenessResult(is_stale=False, context_event_at=None, context_description=None)
    event_at = _lineup_event_timestamp(lineup)
    if event_at is None:
        return ContextStalenessResult(is_stale=False, context_event_at=None, context_description=None)
    latest_quote_at = aware(latest_quote_at)
    if event_at <= latest_quote_at:
        return ContextStalenessResult(is_stale=False, context_event_at=event_at, context_description=None)
    return ContextStalenessResult(
        is_stale=True, context_event_at=event_at,
        context_description=f"Lineup status is {lineup.selection_status!r} as of {event_at.isoformat()}.",
    )


def check_context_item_staleness(context_items: list, latest_quote_at: datetime) -> ContextStalenessResult:
    """context_items: current_context_for_match's output for this subject
    (already the latest-per-subject view — see match_context_service.py),
    filtered by the caller to the player/team this market is about."""
    if not context_items:
        return ContextStalenessResult(is_stale=False, context_event_at=None, context_description=None)
    from app.player_modelling.match_context_service import _effective_timestamp

    latest_item = max(context_items, key=_effective_timestamp)
    event_at = aware(_effective_timestamp(latest_item))
    latest_quote_at = aware(latest_quote_at)
    if event_at <= latest_quote_at:
        return ContextStalenessResult(is_stale=False, context_event_at=event_at, context_description=None)
    label = CONTEXT_TYPE_LABELS.get(latest_item.context_type, latest_item.context_type)
    return ContextStalenessResult(is_stale=True, context_event_at=event_at, context_description=f"{label}: {latest_item.summary}")
