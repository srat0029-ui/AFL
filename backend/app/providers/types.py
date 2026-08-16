"""Plain data-transfer objects returned by provider implementations.

These are deliberately decoupled from the SQLAlchemy models: a provider's job is
to describe what an external source (Squiggle, AFL Tables, a bookmaker feed,
manual entry, ...) says, not to know about our database. A future ingestion
step maps these onto ORM rows. Keeping that mapping as a separate layer means
a provider implementation never needs to change just because the schema does,
and vice versa.

Fields are intentionally sport-agnostic (sport_code is a string like "AFL"
rather than a hardcoded AFL type) so a second sport is a new provider
implementation, not a change to these shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

# PlayerStatLine.round_label is AFL-specific (round numbers + finals codes
# like "EF"/"GF") — a deliberate, pragmatic exception to this module's
# otherwise sport-agnostic fields, made because this project is AFL-only;
# revisit if a second sport is ever added (see module docstring). Deferred
# to a type-checking-only import (the `from __future__ import annotations`
# above makes all annotations lazy strings) since importing it eagerly
# pulls in the app.providers.afl package __init__, which circles back to
# this exact module for OddsQuote/Fixture.
if TYPE_CHECKING:
    from app.providers.afl.round_labels import RoundLabel


@dataclass(frozen=True)
class Fixture:
    """One match as reported by a fixture/results source."""

    external_id: str
    sport_code: str
    season_year: int
    round_number: int
    home_team: str
    away_team: str
    scheduled_start: datetime
    status: str
    round_name: str | None = None
    venue_name: str | None = None
    # The provider's own stable id for each team, when it exposes one (e.g.
    # Squiggle's hteamid/ateamid) — kept optional since not every provider
    # will have this. Teams are still matched by (sport, name); this is
    # enrichment only, not the dedup key.
    home_team_external_id: str | None = None
    away_team_external_id: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    home_score_by_quarter: list[int] | None = None
    away_score_by_quarter: list[int] | None = None
    # Sport-specific scoring subcomponents that sum to the total, e.g. AFL's
    # {"goals": 12, "behinds": 14} (worth 6 and 1 point respectively) — kept
    # generic like TeamStatLine.stats rather than hardcoding AFL fields here.
    home_score_breakdown: dict[str, int] | None = None
    away_score_breakdown: dict[str, int] | None = None


@dataclass(frozen=True)
class TeamStatLine:
    """One team's aggregate stats for a single match, as reported by a stats source."""

    match_external_id: str
    sport_code: str
    team_name: str
    recorded_at: datetime
    # Open-ended on purpose: which stats are available (and predictive) is a
    # modelling question for later stages, not something the provider contract
    # should hardcode. e.g. {"disposals": 412, "clearances": 38, "inside_50s": 55}
    stats: dict[str, float] = field(default_factory=dict)
    # A stats source doesn't necessarily share a fixture source's external id
    # scheme (e.g. AFL Tables has no relationship to Squiggle's match ids) —
    # opponent_name + match_date let ingestion resolve this row against an
    # already-ingested Match by (season, team pair, date) instead. Optional
    # since a source that *does* share the fixture id scheme won't need them.
    opponent_name: str | None = None
    match_date: date | None = None


@dataclass(frozen=True)
class PlayerStatLine:
    """One player's stats for a single match, as reported by a stats source.

    Resolved to a Match primarily by (season_year, round_label, team_name)
    rather than a shared match id or date — the AFL Tables page this is
    scraped from (a team's season "game by game" grid) publishes round
    labels (numbered rounds AND finals codes like "EF"/"GF" — see
    app/providers/afl/round_labels.py), not match dates, per cell. A safe
    fallback resolver in app/ingestion/player_stats.py handles the residual
    cases where the source's round label doesn't line up with this
    project's own round numbering (see that module's docstring).
    """

    sport_code: str
    season_year: int
    round_label: RoundLabel
    team_name: str
    player_name: str  # as published, e.g. "Acres, Blake"
    # The source's stable per-player identifier (for AFL Tables, the player
    # page's path, e.g. "players/B/Blake_Acres.html") — the real identity
    # anchor; player_name alone is not unique or stable enough to key on.
    player_source_id: str
    recorded_at: datetime
    stats: dict[str, float] = field(default_factory=dict)
    jumper_number: int | None = None
    subbed_on: bool = False
    subbed_off: bool = False


@dataclass(frozen=True)
class OddsQuote:
    """A single bookmaker's price for one market selection at a point in time."""

    match_external_id: str
    sport_code: str
    bookmaker: str
    market_type: str  # e.g. "h2h", "line", "total_points"
    selection: str  # e.g. a team name, or "over"/"under"
    price_decimal: float
    recorded_at: datetime
    line_value: float | None = None  # e.g. the handicap or total line, when applicable
    is_closing_line: bool = False
    source: str = "manual"


@dataclass(frozen=True)
class ProviderEvent:
    """One upcoming/live event as listed by a player-prop odds provider —
    deliberately separate from Fixture (our fixture source, Squiggle) since
    this is a DIFFERENT external source with its own id scheme; ingestion
    resolves this to an already-ingested Match by (home_team, away_team,
    commence_time), never creates a Match from it. See
    app/providers/player_prop_odds.py."""

    provider: str  # e.g. "the_odds_api"
    event_id: str  # the provider's own event id - opaque, cached on Match.external_ids once resolved
    sport_key: str  # the provider's own sport key, e.g. "aussierules_afl"
    home_team: str  # as published by the provider - not assumed to match our Team.name exactly
    away_team: str
    commence_time: datetime


@dataclass(frozen=True)
class PlayerPropQuote:
    """One bookmaker's price for one player-prop market outcome, as reported
    by an odds provider — the player-prop analogue of OddsQuote.

    Deliberately raw/unmapped: `market_key` and `selection` are the
    PROVIDER'S own vocabulary (e.g. market_key="player_disposals_over",
    selection="Over"), not yet translated into this project's PlayerMarket/
    LineType. That translation is ingestion's job (see
    app/player_modelling/prop_market_mapping.py) so a provider
    implementation never needs to know this project's internal market
    representation, and a market this project doesn't (yet) model can still
    be reported rather than silently dropped by the provider layer itself.

    player_name is the provider's own text and is NOT assumed to resolve
    cleanly to a Player row — that's a separate, careful resolution step
    (see app/player_modelling/prop_player_resolution.py) precisely because
    silently trusting an external name would risk attaching a real
    person's odds to the wrong player.
    """

    provider: str  # e.g. "the_odds_api"
    event_id: str  # the provider's event id this quote belongs to
    sport_code: str
    bookmaker_key: str  # the provider's own bookmaker key, e.g. "sportsbet"
    bookmaker_title: str  # display name, e.g. "Sportsbet"
    bookmaker_region: str  # e.g. "au"
    market_key: str  # the provider's raw market key, e.g. "player_disposals_over"
    player_name: str  # as published by the provider
    selection: str  # the provider's raw outcome name, e.g. "Over", "Under", "Yes"
    price_decimal: float
    bookmaker_last_update: datetime  # the bookmaker's own last-updated timestamp for this market
    fetched_at: datetime  # when WE retrieved this quote - distinct from the bookmaker's own timestamp
    threshold: float | None = None  # the provider's "point" value; None for markets with no line (e.g. anytime goalscorer)
    raw_outcome: dict | None = None  # the raw outcome object, for traceability/debugging - not re-derived from
