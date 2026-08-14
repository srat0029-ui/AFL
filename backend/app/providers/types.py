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
from datetime import datetime


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
    home_score: int | None = None
    away_score: int | None = None
    home_score_by_quarter: list[int] | None = None
    away_score_by_quarter: list[int] | None = None


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


@dataclass(frozen=True)
class PlayerStatLine:
    """One player's stats for a single match, as reported by a stats source."""

    match_external_id: str
    sport_code: str
    team_name: str
    player_name: str
    recorded_at: datetime
    stats: dict[str, float] = field(default_factory=dict)


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
