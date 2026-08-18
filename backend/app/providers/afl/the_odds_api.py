"""PlayerPropOddsProvider implementation backed by The Odds API
(https://the-odds-api.com/), a legitimate, documented, paid/free-tier REST
API — not a bookmaker-website scrape (see this stage's brief: "Do not
scrape bookmaker websites in this stage. Use legitimate APIs only.").

Verified 2026-08-16 against the CURRENT official documentation at
the-odds-api.com/liveapi/guides/v4/ and the-odds-api.com/sports-odds-data/
betting-markets.html (not assumed from memory - two similarly-named but
UNAFFILIATED lookalike domains, theoddsapi.com and oddspapi.io, surfaced in
search results during this research and were deliberately NOT used as a
source):

- AFL sport key: "aussierules_afl".
- Player props require the EVENT-SPECIFIC endpoint
  (/v4/sports/{sport}/events/{eventId}/odds), not the standard
  /v4/sports/{sport}/odds endpoint used for match-level markets (h2h/
  spreads/totals) - the standard endpoint does not return player props at
  all, regardless of the `markets` parameter.
- GET /v4/sports/{sport}/events (listing event ids) does NOT count against
  the usage quota - see list_events(). This is exactly why this provider
  is split into a free "list events" step and a paid "fetch quotes for one
  event" step (PlayerPropOddsProvider's shape), rather than one call.
- Quota cost for the event-odds endpoint = [unique markets ACTUALLY
  RETURNED in the response] x [number of regions requested] - not markets
  requested. Requesting a market a bookmaker doesn't offer costs nothing
  for that market.
- Every response carries x-requests-remaining / x-requests-used /
  x-requests-last headers - parsed into QuotaStatus so a caller can report
  real usage without a separate account-status call.
- Player-prop outcomes carry the player's name in the outcome's
  `description` field, not `name` (`name` holds "Over"/"Under"/"Yes" - the
  side, not who it's about) - verified against the official docs' own
  example response.
- Documented AFL player-prop market keys (see _KNOWN_AFL_PLAYER_PROP_MARKETS
  below) go well beyond disposals/goals (marks, tackles, clearances, kicks,
  handballs, AFL Fantasy points, first/last goalscorer) - this project only
  MODELS disposals and goals today (see app/player_modelling/market.py), so
  only requests the subset of markets it can actually turn into a
  projection comparison (see MODELLED_MARKET_KEYS). The rest are listed
  here for the market-availability audit (Section 24) to report honestly on
  what the provider offers that this app doesn't yet use, without ever
  silently fetching (and paying for) markets nothing consumes.

None of the above is a substitute for a real fetch against a live API key -
see this stage's report for exactly what a real current-round request
returned, versus what the documentation claims is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.providers.player_prop_odds import PlayerPropOddsProvider, PlayerPropOddsResult, QuotaStatus
from app.providers.types import PlayerPropQuote, ProviderEvent, TeamOddsQuote

BASE_URL = "https://api.the-odds-api.com/v4"
PROVIDER_NAME = "the_odds_api"
AFL_SPORT_KEY = "aussierules_afl"
DEFAULT_REGION = "au"  # AFL player props are only offered by Australian-region bookmakers

# Every AFL player-prop market key documented at the-odds-api.com as of this
# stage's research (see module docstring) - used for the market-availability
# audit, NOT all requested on every refresh (see MODELLED_MARKET_KEYS).
KNOWN_AFL_PLAYER_PROP_MARKET_KEYS: list[str] = [
    "player_disposals",
    "player_disposals_over",
    "player_goal_scorer_first",
    "player_goal_scorer_last",
    "player_goal_scorer_anytime",
    "player_goals_scored_over",
    "player_marks_over",
    "player_marks_most",
    "player_tackles_over",
    "player_tackles_most",
    "player_afl_fantasy_points",
    "player_afl_fantasy_points_over",
    "player_afl_fantasy_points_most",
    "player_clearances_over",
    "player_kicks_over",
    "player_handballs_over",
]

# The subset this project can actually turn into a model-vs-market
# comparison today - only disposals and goals have a promoted projection
# model (see app/player_modelling/market.py's PlayerMarket enum and its
# note that TACKLES/MARKS have no projection model behind them yet). Only
# these are ever requested from the API, so refresh-prop-odds never spends
# quota on a market this app can't do anything with.
MODELLED_MARKET_KEYS: list[str] = [
    "player_disposals",
    "player_disposals_over",
    "player_goal_scorer_anytime",
    "player_goals_scored_over",
]


# Section 23 of the diversification stage: standard AFL match markets
# (NOT player props). Fetched from the standard /v4/sports/{sport}/odds
# endpoint (see module docstring) - one call covers every upcoming event
# in a single request, unlike the event-specific player-prop endpoint, so
# this is considerably cheaper per match covered.
STANDARD_MARKET_KEYS: list[str] = ["h2h", "spreads", "totals"]


@dataclass(frozen=True)
class StandardMatchOddsResult:
    events: list[ProviderEvent]
    quotes: list[TeamOddsQuote]
    quota: QuotaStatus
    markets_requested: list[str]
    markets_returned: list[str]


class TheOddsApiError(Exception):
    """Raised for any request failure (network, non-2xx, malformed body) -
    callers (the refresh CLI) catch this per-event so one bad event doesn't
    abort an entire refresh run, matching this codebase's existing
    per-item try/except convention (see e.g. backfill_player_stats)."""


def _parse_quota_headers(headers: httpx.Headers) -> QuotaStatus:
    def _int_or_none(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    return QuotaStatus(
        requests_used=_int_or_none(headers.get("x-requests-used")),
        requests_remaining=_int_or_none(headers.get("x-requests-remaining")),
        last_request_cost=_int_or_none(headers.get("x-requests-last")),
    )


@dataclass(frozen=True)
class _RawEvent:
    id: str
    sport_key: str
    home_team: str
    away_team: str
    commence_time: datetime


def _parse_commence_time(raw: str) -> datetime:
    # The API returns ISO 8601 with a trailing "Z" - Python's fromisoformat
    # only accepts "+00:00" before 3.11's relaxed parsing; normalise explicitly
    # rather than assume the running interpreter's version.
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


class TheOddsApiProvider(PlayerPropOddsProvider):
    def __init__(self, api_key: str, client: httpx.Client | None = None, timeout_seconds: float = 20.0):
        self._api_key = api_key
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=timeout_seconds)

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    def list_events(self, sport_code: str) -> list[ProviderEvent]:
        if not self.is_available:
            raise TheOddsApiError("THE_ODDS_API_KEY is not configured")
        if sport_code != "AFL":
            raise ValueError(f"TheOddsApiProvider only supports AFL, got {sport_code!r}")

        try:
            response = self._client.get(
                f"/sports/{AFL_SPORT_KEY}/events", params={"apiKey": self._api_key}
            )
        except httpx.HTTPError as exc:
            raise TheOddsApiError(f"request to list AFL events failed: {exc}") from exc
        if response.status_code != 200:
            raise TheOddsApiError(
                f"AFL events list returned HTTP {response.status_code}: {response.text[:300]}"
            )

        events: list[ProviderEvent] = []
        for row in response.json():
            events.append(
                ProviderEvent(
                    provider=PROVIDER_NAME,
                    event_id=row["id"],
                    sport_key=row["sport_key"],
                    home_team=row["home_team"],
                    away_team=row["away_team"],
                    commence_time=_parse_commence_time(row["commence_time"]),
                )
            )
        return events

    def get_player_prop_quotes(
        self, sport_code: str, event: ProviderEvent, market_keys: list[str]
    ) -> PlayerPropOddsResult:
        if not self.is_available:
            raise TheOddsApiError("THE_ODDS_API_KEY is not configured")
        if sport_code != "AFL":
            raise ValueError(f"TheOddsApiProvider only supports AFL, got {sport_code!r}")
        if not market_keys:
            return PlayerPropOddsResult(quotes=[], quota=QuotaStatus(), markets_requested=[], markets_returned=[])

        try:
            response = self._client.get(
                f"/sports/{AFL_SPORT_KEY}/events/{event.event_id}/odds",
                params={
                    "apiKey": self._api_key,
                    "regions": DEFAULT_REGION,
                    "markets": ",".join(market_keys),
                    "oddsFormat": "decimal",
                },
            )
        except httpx.HTTPError as exc:
            raise TheOddsApiError(f"request for event {event.event_id} odds failed: {exc}") from exc
        if response.status_code != 200:
            raise TheOddsApiError(
                f"event {event.event_id} odds returned HTTP {response.status_code}: {response.text[:300]}"
            )

        quota = _parse_quota_headers(response.headers)
        fetched_at = datetime.now(timezone.utc)
        body = response.json()

        quotes: list[PlayerPropQuote] = []
        markets_returned: set[str] = set()
        for bookmaker in body.get("bookmakers", []):
            bookmaker_key = bookmaker["key"]
            bookmaker_title = bookmaker.get("title", bookmaker_key)
            for market in bookmaker.get("markets", []):
                market_key = market["key"]
                markets_returned.add(market_key)
                market_last_update = _parse_commence_time(market["last_update"]) if market.get("last_update") else fetched_at
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description")
                    if not player_name:
                        # A market row with no player attached (shouldn't
                        # happen for a player-prop market key, but the docs
                        # only promise "relevant markets will have a
                        # description field" - refuse to guess a player
                        # rather than attach this quote to nobody).
                        continue
                    quotes.append(
                        PlayerPropQuote(
                            provider=PROVIDER_NAME,
                            event_id=event.event_id,
                            sport_code=sport_code,
                            bookmaker_key=bookmaker_key,
                            bookmaker_title=bookmaker_title,
                            bookmaker_region=DEFAULT_REGION,
                            market_key=market_key,
                            player_name=player_name,
                            selection=outcome["name"],
                            price_decimal=float(outcome["price"]),
                            bookmaker_last_update=market_last_update,
                            fetched_at=fetched_at,
                            threshold=outcome.get("point"),
                            raw_outcome=outcome,
                        )
                    )

        return PlayerPropOddsResult(
            quotes=quotes,
            quota=quota,
            markets_requested=list(market_keys),
            markets_returned=sorted(markets_returned),
        )

    def get_standard_match_odds(
        self, sport_code: str, market_keys: list[str] | None = None
    ) -> StandardMatchOddsResult:
        """Section 23: standard match-level markets (h2h/spreads/totals)
        via the non-event-specific /v4/sports/{sport}/odds endpoint - one
        call returns every upcoming event with odds already attached, so
        (unlike player props) there is no separate free "list events"
        step here."""
        if not self.is_available:
            raise TheOddsApiError("THE_ODDS_API_KEY is not configured")
        if sport_code != "AFL":
            raise ValueError(f"TheOddsApiProvider only supports AFL, got {sport_code!r}")
        keys = market_keys if market_keys is not None else STANDARD_MARKET_KEYS

        try:
            response = self._client.get(
                f"/sports/{AFL_SPORT_KEY}/odds",
                params={
                    "apiKey": self._api_key,
                    "regions": DEFAULT_REGION,
                    "markets": ",".join(keys),
                    "oddsFormat": "decimal",
                },
            )
        except httpx.HTTPError as exc:
            raise TheOddsApiError(f"request for standard AFL match odds failed: {exc}") from exc
        if response.status_code != 200:
            raise TheOddsApiError(
                f"standard AFL match odds returned HTTP {response.status_code}: {response.text[:300]}"
            )

        quota = _parse_quota_headers(response.headers)
        fetched_at = datetime.now(timezone.utc)

        events: list[ProviderEvent] = []
        quotes: list[TeamOddsQuote] = []
        markets_returned: set[str] = set()
        for event_row in response.json():
            event = ProviderEvent(
                provider=PROVIDER_NAME,
                event_id=event_row["id"],
                sport_key=event_row["sport_key"],
                home_team=event_row["home_team"],
                away_team=event_row["away_team"],
                commence_time=_parse_commence_time(event_row["commence_time"]),
            )
            events.append(event)
            for bookmaker in event_row.get("bookmakers", []):
                bookmaker_key = bookmaker["key"]
                bookmaker_title = bookmaker.get("title", bookmaker_key)
                for market in bookmaker.get("markets", []):
                    market_key = market["key"]
                    markets_returned.add(market_key)
                    market_last_update = _parse_commence_time(market["last_update"]) if market.get("last_update") else fetched_at
                    for outcome in market.get("outcomes", []):
                        quotes.append(
                            TeamOddsQuote(
                                provider=PROVIDER_NAME,
                                event_id=event.event_id,
                                sport_code=sport_code,
                                bookmaker_key=bookmaker_key,
                                bookmaker_title=bookmaker_title,
                                bookmaker_region=DEFAULT_REGION,
                                market_key=market_key,
                                selection=outcome["name"],
                                price_decimal=float(outcome["price"]),
                                bookmaker_last_update=market_last_update,
                                fetched_at=fetched_at,
                                line_value=outcome.get("point"),
                            )
                        )

        return StandardMatchOddsResult(
            events=events,
            quotes=quotes,
            quota=quota,
            markets_requested=list(keys),
            markets_returned=sorted(markets_returned),
        )
