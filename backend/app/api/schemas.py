"""Pydantic request/response models for the API layer.

Kept separate from the SQLAlchemy models (app/models/) so the API's public
shape can evolve independently of the DB schema — e.g. nesting team/venue
details inline in a match response without that shape leaking back into the
ORM layer.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class TeamSummary(BaseModel):
    id: int
    name: str
    short_name: str
    primary_colour: str | None
    secondary_colour: str | None

    model_config = {"from_attributes": True}


class VenueSummary(BaseModel):
    id: int
    name: str
    city: str | None

    model_config = {"from_attributes": True}


class MatchSummary(BaseModel):
    id: int
    season_year: int
    round_number: int
    status: str
    scheduled_start: datetime
    home_team: TeamSummary
    away_team: TeamSummary
    venue: VenueSummary | None
    home_score: int | None
    away_score: int | None


class MarketType(str, Enum):
    H2H = "h2h"
    LINE = "line"
    TOTAL = "total"


class OddsQuoteCreate(BaseModel):
    bookmaker_name: str = Field(..., min_length=1, max_length=64)
    market_type: MarketType
    selection: str = Field(..., min_length=1, max_length=64)
    line_value: float | None = None
    price_decimal: float = Field(..., gt=1.0, le=1000.0)
    recorded_at: datetime | None = None
    is_closing_line: bool = False

    @model_validator(mode="after")
    def _validate_market_shape(self) -> "OddsQuoteCreate":
        if self.market_type in (MarketType.LINE, MarketType.TOTAL) and self.line_value is None:
            raise ValueError(f"line_value is required for market_type={self.market_type.value!r}")
        if self.market_type == MarketType.H2H and self.line_value is not None:
            raise ValueError("line_value must not be set for market_type='h2h'")
        if self.market_type == MarketType.TOTAL and self.selection.lower() not in ("over", "under"):
            raise ValueError("selection must be 'over' or 'under' for market_type='total'")
        return self


class OddsQuoteRead(BaseModel):
    id: int
    match_id: int
    bookmaker_name: str
    market_type: str
    selection: str
    line_value: float | None
    price_decimal: float
    recorded_at: datetime
    source: str
    is_closing_line: bool


class MarketEdgeRead(BaseModel):
    match_id: int
    odds_quote_id: int
    market_type: str
    selection: str
    line_value: float | None
    bookmaker_name: str
    price_decimal: float
    model_probability: float
    secondary_model_probability: float | None
    market_implied_probability: float
    fair_market_probability: float
    overround_removed: bool
    fair_odds: float
    model_edge: float
    expected_value: float
    edge_tier: str
    confidence_tier: str
    confidence_reasons: list[str]

    model_config = {"from_attributes": True}


class MatchPredictionsRead(BaseModel):
    match_id: int
    elo_home_win_probability: float
    poisson_home_win_probability: float
    poisson_draw_probability: float
    poisson_away_win_probability: float
    poisson_home_expected_score: float
    poisson_away_expected_score: float
    poisson_expected_total_points: float
    poisson_expected_margin: float

    model_config = {"from_attributes": True}


class DashboardEntry(BaseModel):
    match: MatchSummary
    predictions: MatchPredictionsRead
    best_edge: MarketEdgeRead | None


class BacktestSegmentRead(BaseModel):
    label: str
    n: int
    metrics: dict[str, float]

    model_config = {"from_attributes": True}


class CalibrationBucketRead(BaseModel):
    bucket: str
    n: int
    avg_predicted: float | None
    actual_rate: float | None


class WinProbReportRead(BaseModel):
    model_name: str
    overall: BacktestSegmentRead
    by_season: list[BacktestSegmentRead]
    by_team: list[BacktestSegmentRead]
    by_conviction: list[BacktestSegmentRead]
    calibration: list[CalibrationBucketRead]

    model_config = {"from_attributes": True}


class ScoringReportRead(BaseModel):
    overall: BacktestSegmentRead
    by_season: list[BacktestSegmentRead]

    model_config = {"from_attributes": True}


class TrackedSelectionRead(BaseModel):
    match_id: int
    market_type: str
    selection: str
    line_value: float | None
    bookmaker_name: str
    price_decimal: float
    is_closing_line: bool
    model_probability: float
    won: bool | None
    pnl_units: float

    model_config = {"from_attributes": True}


class LoggedOddsReportRead(BaseModel):
    n_total: int
    n_resolved: int
    n_void: int
    win_rate: float | None
    roi_pct: float | None
    yield_pct: float | None
    total_pnl_units: float
    brier_score: float | None
    log_loss: float | None
    selections: list[TrackedSelectionRead]

    model_config = {"from_attributes": True}


class BacktestOverview(BaseModel):
    elo: WinProbReportRead
    poisson_win: WinProbReportRead
    poisson_scoring: ScoringReportRead
    logged_odds: LoggedOddsReportRead
