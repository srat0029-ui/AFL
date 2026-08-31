"""The alert vocabulary: 8 structured alert types, 3 severity levels, and
the single `Alert` shape every detector in this package produces — so the
API/UI/snapshot layers only ever need to handle one dataclass, not one per
check.

Severity is a plain, documented magnitude rule per type (see SEVERITY_RULES'
callers in each detector module) — never a fitted/learned score. Language
is deliberately neutral throughout: an alert reports a MEASURED discrepancy
between two prices/states, never a verdict on which one is "wrong" (a
bookmaker may be intentionally short a line for liquidity reasons this
engine has no visibility into; a stale market may simply not have refreshed
yet for reasons unrelated to the context event).
"""

from dataclasses import dataclass
from datetime import datetime

# --- Alert types (item 1) ---------------------------------------------------

MODEL_VS_MARKET_DIVERGENCE = "MODEL_VS_MARKET_DIVERGENCE"
BOOKMAKER_VS_CONSENSUS_OUTLIER = "BOOKMAKER_VS_CONSENSUS_OUTLIER"
STALE_AFTER_LINEUP_CHANGE = "STALE_AFTER_LINEUP_CHANGE"
STALE_AFTER_CONTEXT_CHANGE = "STALE_AFTER_CONTEXT_CHANGE"
NON_MONOTONIC_PLAYER_PRICE_CURVE = "NON_MONOTONIC_PLAYER_PRICE_CURVE"
ADJACENT_THRESHOLD_JUMP = "ADJACENT_THRESHOLD_JUMP"
TEAM_MARKET_INTERNAL_INCONSISTENCY = "TEAM_MARKET_INTERNAL_INCONSISTENCY"
LARGE_MARKET_DISPERSION = "LARGE_MARKET_DISPERSION"

# Market-movement anomalies (item 5) — a distinct capability from item 1's
# point-in-time checks above, so distinct types; same Alert shape/severity
# convention.
SHARP_MARKET_MOVE_MODEL_STABLE = "SHARP_MARKET_MOVE_MODEL_STABLE"
BOOKMAKER_MOVED_VS_STABLE_CONSENSUS = "BOOKMAKER_MOVED_VS_STABLE_CONSENSUS"
CONSENSUS_MOVED_VS_STALE_BOOKMAKER = "CONSENSUS_MOVED_VS_STALE_BOOKMAKER"

ALL_ALERT_TYPES: tuple[str, ...] = (
    MODEL_VS_MARKET_DIVERGENCE,
    BOOKMAKER_VS_CONSENSUS_OUTLIER,
    STALE_AFTER_LINEUP_CHANGE,
    STALE_AFTER_CONTEXT_CHANGE,
    NON_MONOTONIC_PLAYER_PRICE_CURVE,
    ADJACENT_THRESHOLD_JUMP,
    TEAM_MARKET_INTERNAL_INCONSISTENCY,
    LARGE_MARKET_DISPERSION,
    SHARP_MARKET_MOVE_MODEL_STABLE,
    BOOKMAKER_MOVED_VS_STABLE_CONSENSUS,
    CONSENSUS_MOVED_VS_STALE_BOOKMAKER,
)

# --- Severity ----------------------------------------------------------------

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
ALL_SEVERITIES: tuple[str, ...] = (SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_CRITICAL)


@dataclass(frozen=True)
class BookmakerPriceEntry:
    bookmaker_name: str
    price_decimal: float
    recorded_at: datetime
    eligibility: str


@dataclass(frozen=True)
class ModelRiskFlagEntry:
    code: str
    description: str


@dataclass(frozen=True)
class Alert:
    """One structured anomaly finding. Every field item 1 requires is
    present on every alert regardless of type (nullable where a given
    alert type has no such axis — e.g. a team-market alert has no
    threshold) so a downstream consumer never needs type-specific parsing
    to read the common fields."""

    alert_type: str
    severity: str
    reason_code: str  # short machine-readable summary, e.g. "divergence_18.4pp"
    detail: str  # neutral, human-readable — never "wrong"/"mispriced"

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
    bookmaker_prices: list[BookmakerPriceEntry]

    freshness: str | None
    model_version: str | None
    lineup_status: str | None
    context_state: str | None
    model_risk_flags: list[ModelRiskFlagEntry]

    generated_at: datetime

    # The single number the detector used to decide this alert's severity
    # (see detector.py's _severity() call sites) - carried through
    # unchanged so the Alert Precision + Trader Prioritisation stage's
    # priority score (app/market_monitor/priority.py) can use the EXACT
    # same evidence the raw detection already computed, rather than
    # re-deriving or parsing it back out of `detail`/`reason_code` text.
    # Units vary by alert_type (probability points for divergence/dispersion/
    # movement, a ratio for adjacent-threshold jumps, etc.) - always
    # documented at the call site that sets it.
    magnitude: float | None = None


def alert_as_dict(a: Alert) -> dict:
    return {
        "alert_type": a.alert_type,
        "severity": a.severity,
        "reason_code": a.reason_code,
        "detail": a.detail,
        "match_id": a.match_id,
        "home_team": a.home_team,
        "away_team": a.away_team,
        "player_id": a.player_id,
        "player_name": a.player_name,
        "team_id": a.team_id,
        "market_type": a.market_type,
        "selection": a.selection,
        "threshold": a.threshold,
        "line_value": a.line_value,
        "model_probability": a.model_probability,
        "model_fair_odds": a.model_fair_odds,
        "market_consensus_probability": a.market_consensus_probability,
        "bookmaker_prices": [
            {"bookmaker_name": b.bookmaker_name, "price_decimal": b.price_decimal, "recorded_at": b.recorded_at, "eligibility": b.eligibility}
            for b in a.bookmaker_prices
        ],
        "freshness": a.freshness,
        "model_version": a.model_version,
        "lineup_status": a.lineup_status,
        "context_state": a.context_state,
        "model_risk_flags": [{"code": f.code, "description": f.description} for f in a.model_risk_flags],
        "generated_at": a.generated_at,
        "magnitude": a.magnitude,
    }
