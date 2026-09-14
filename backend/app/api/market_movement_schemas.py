"""Response schemas for the Market Movement Explorer API
(/api/v1/market-movement/*) — see app/player_modelling/market_movement_series.py
for the read-only composition layer these mirror field-for-field."""

from pydantic import BaseModel

from app.api.schemas import UtcDatetime


class MatchContextRead(BaseModel):
    match_id: int
    home_team: str
    away_team: str
    scheduled_start: UtcDatetime
    status: str


class MatchWithHistoryRead(BaseModel):
    match: MatchContextRead
    n_odds_quotes: int
    n_player_prop_quotes: int
    n_model_observations: int
    earliest_observed_at: UtcDatetime | None
    latest_observed_at: UtcDatetime | None


class TeamMarketOptionRead(BaseModel):
    market_type: str
    selection: str
    line_value: float | None
    n_quotes: int
    bookmakers: list[str]
    first_observed_at: UtcDatetime
    latest_observed_at: UtcDatetime
    has_model_series: bool


class PlayerMarketOptionRead(BaseModel):
    player_id: int
    player_name: str
    market_type: str
    line_type: str
    threshold: float
    n_quotes: int
    bookmakers: list[str]
    first_observed_at: UtcDatetime
    latest_observed_at: UtcDatetime
    has_model_series: bool


class MatchMarketOptionsRead(BaseModel):
    match: MatchContextRead
    team_markets: list[TeamMarketOptionRead]
    player_markets: list[PlayerMarketOptionRead]


class BookmakerQuotePointRead(BaseModel):
    bookmaker_name: str
    price_decimal: float
    raw_implied_probability: float
    recorded_at: UtcDatetime
    hours_to_kickoff: float
    source: str
    is_closing_line: bool


class ConsensusPointRead(BaseModel):
    as_of: UtcDatetime
    hours_to_kickoff: float
    consensus_probability: float
    n_bookmakers: int
    n_devigged: int
    spread: float


class ModelObservationPointRead(BaseModel):
    value_type: str
    value_kind: str
    value: float
    model_name: str
    model_version: str
    recorded_at: UtcDatetime
    hours_to_kickoff: float
    lineup_status: str | None


class LineupStatusChangeRead(BaseModel):
    from_status: str | None
    to_status: str | None
    changed_at: UtcDatetime
    hours_to_kickoff: float
    value_changed_at_same_observation: bool
    value_before: float
    value_after: float


class SeriesEndpointRead(BaseModel):
    probability: float
    recorded_at: UtcDatetime
    hours_to_kickoff: float
    price_decimal: float | None
    bookmaker_name: str | None


class LargestMovementRead(BaseModel):
    from_probability: float
    to_probability: float
    absolute_change: float
    at: UtcDatetime
    hours_to_kickoff: float


class SeriesSummaryRead(BaseModel):
    label: str
    n_observations: int
    insufficient_history: bool
    first_observed: SeriesEndpointRead | None
    latest_observed_pre_kickoff: SeriesEndpointRead | None
    total_probability_change: float | None
    largest_single_movement: LargestMovementRead | None


class MarketMovementSeriesRead(BaseModel):
    match: MatchContextRead
    identity_label: str
    methodology_notes: list[str]
    bookmaker_quotes: list[BookmakerQuotePointRead]
    bookmaker_quotes_post_kickoff: list[BookmakerQuotePointRead]
    consensus_series: list[ConsensusPointRead]
    model_observations: list[ModelObservationPointRead]
    model_observations_post_kickoff: list[ModelObservationPointRead]
    model_projected_mean_observations: list[ModelObservationPointRead]
    lineup_status_changes: list[LineupStatusChangeRead]
    bookmaker_summary: SeriesSummaryRead
    consensus_summary: SeriesSummaryRead | None
    model_summary: SeriesSummaryRead | None
