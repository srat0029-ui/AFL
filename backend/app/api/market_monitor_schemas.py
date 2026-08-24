"""Response schemas for the B2B Market Anomaly / Trading QA API
(/api/v1/market-monitor/*) — a separately-versioned, read-only surface for
another engineering team, same convention as pricing_schemas.py (kept
independent of the internal-product app.api.schemas module)."""

from pydantic import BaseModel

from app.api.pricing_schemas import ModelRiskFlagRead
from app.api.schemas import UtcDatetime


class BookmakerPriceRead(BaseModel):
    bookmaker_name: str
    price_decimal: float
    recorded_at: UtcDatetime
    eligibility: str


class AnomalyAlertRead(BaseModel):
    alert_type: str
    severity: str
    reason_code: str
    detail: str

    match_id: int
    home_team: str
    away_team: str
    player_id: int | None
    player_name: str | None
    team_id: int | None

    market_type: str
    selection: str | None
    threshold: float | None
    line_value: float | None

    model_probability: float | None
    model_fair_odds: float | None
    market_consensus_probability: float | None
    bookmaker_prices: list[BookmakerPriceRead]

    freshness: str | None
    model_version: str | None
    lineup_status: str | None
    context_state: str | None
    model_risk_flags: list[ModelRiskFlagRead]

    generated_at: UtcDatetime


class MatchAnomaliesRead(BaseModel):
    match_id: int
    home_team: str
    away_team: str
    alerts: list[AnomalyAlertRead]


class AnomalyTypeCount(BaseModel):
    alert_type: str
    count: int


class SeverityCount(BaseModel):
    severity: str
    count: int


class AnomalySummaryRead(BaseModel):
    generated_at: UtcDatetime
    n_matches_scanned: int
    total_anomalies: int
    by_type: list[AnomalyTypeCount]
    by_severity: list[SeverityCount]


class AnomalyListRead(BaseModel):
    generated_at: UtcDatetime
    n_matches_scanned: int
    total: int
    alerts: list[AnomalyAlertRead]
