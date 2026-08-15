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

from dataclasses import dataclass, field
from datetime import date, datetime


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

    Resolved to a Match by (season_year, round_number, team_name) rather
    than a shared match id or date — the AFL Tables page this is scraped
    from (a team's season "game by game" grid) publishes round labels, not
    match dates, per cell. See app/ingestion/player_stats.py for the
    resolution against already-ingested Match/Round rows, and why round
    number is actually a more robust key here than date (no timezone/date
    off-by-one edge cases to worry about).
    """

    sport_code: str
    season_year: int
    round_number: int
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
