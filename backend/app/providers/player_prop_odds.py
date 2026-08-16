"""PlayerPropOddsProvider: contract for anything that can supply bookmaker
player-prop odds.

Mirrors app/providers/odds.py's OddsProvider (same "swap the implementation,
not the consumer" seam), but kept as a separate interface rather than
extending OddsProvider: player-prop odds are fetched per-event from a
provider that charges API quota per call (see The Odds API's cost model,
audited in this stage's report), so this contract is deliberately shaped
around "list events for free, then fetch quotes for one event at a time" —
a genuinely different access pattern from OddsProvider's single get_odds
call, not just the same thing with a different DTO.

The Odds API is the first (and, as of this stage, only) implementation —
see app/providers/afl/the_odds_api.py. Everything downstream (matching,
storage, edge calculations, Prop Insights, UI) consumes PlayerPropQuote/
ProviderEvent, never The Odds API's raw response shape directly, so a
second provider is a new class implementing this interface, not a rewrite.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.providers.types import PlayerPropQuote, ProviderEvent


@dataclass(frozen=True)
class QuotaStatus:
    """API usage-quota state as reported by the provider's own response
    headers (when it exposes them) — None fields mean "provider didn't
    report this", not "zero", so callers must not treat None as 0."""

    requests_used: int | None = None
    requests_remaining: int | None = None
    last_request_cost: int | None = None


@dataclass(frozen=True)
class PlayerPropOddsResult:
    """The outcome of one get_player_prop_quotes call: the normalized-ish
    quotes plus enough bookkeeping to report accurately on quota and
    coverage (Sections 9/24) without a second round-trip."""

    quotes: list[PlayerPropQuote]
    quota: QuotaStatus
    markets_requested: list[str] = field(default_factory=list)
    # Unique market keys actually present in the response - what the provider
    # actually charged for (its own cost formula is markets-returned x
    # regions, not markets-requested), and the honest signal for "does this
    # bookmaker/event actually have this market" rather than assuming the
    # request succeeded just because it didn't error.
    markets_returned: list[str] = field(default_factory=list)


class PlayerPropOddsProvider(ABC):
    """Supplies player-prop bookmaker quotes for upcoming events."""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """False when the provider can't be used right now (e.g. no API key
        configured) - callers must check this and degrade gracefully
        (report "provider unavailable", never crash) rather than call the
        fetch methods and handle an exception."""
        raise NotImplementedError

    @abstractmethod
    def list_events(self, sport_code: str) -> list[ProviderEvent]:
        """List upcoming/live events for a sport. Implementations must use
        whatever endpoint the provider documents as NOT counting against
        quota for this (The Odds API's plain events list does not) - this
        method existing separately from get_player_prop_quotes is exactly
        why that's possible."""
        raise NotImplementedError

    @abstractmethod
    def get_player_prop_quotes(
        self, sport_code: str, event: ProviderEvent, market_keys: list[str]
    ) -> PlayerPropOddsResult:
        """Fetch player-prop quotes for one event's given market keys. This
        costs API quota - callers are responsible for not calling this more
        often than necessary (see app/player_modelling/prop_odds_quota.py),
        this method itself does no rate limiting or caching of its own."""
        raise NotImplementedError
