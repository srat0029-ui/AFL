"""Structured uncertainty flags (Weekly Bet Review + Decision Support
stage, Section 11) — named, checkable codes (never free-form prose),
every one derived from data this app already computes elsewhere. Mirrors
the existing REASON_* convention in opportunity_reason_codes.py, just for
the caution side rather than the supporting side.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.player_modelling.player_recent_form import load_recent_form

FLAG_TEAMS_NOT_CONFIRMED = "TEAMS_NOT_CONFIRMED"
FLAG_SINGLE_BOOK_MARKET = "SINGLE_BOOK_MARKET"
FLAG_EARLY_MARKET = "EARLY_MARKET"
FLAG_LARGE_BOOKMAKER_DISAGREEMENT = "LARGE_BOOKMAKER_DISAGREEMENT"
FLAG_MODEL_WEAKER_IN_REGION = "MODEL_WEAKER_IN_REGION"
FLAG_ELITE_PLAYER_CONSERVATIVE_MODEL = "ELITE_PLAYER_CONSERVATIVE_MODEL"
FLAG_RARE_EVENT_THRESHOLD = "RARE_EVENT_THRESHOLD"
FLAG_HIGH_TOG_VOLATILITY = "HIGH_TOG_VOLATILITY"
FLAG_RECENT_FORM_DISAGREES = "RECENT_FORM_DISAGREES"

FLAG_LABELS: dict[str, str] = {
    FLAG_TEAMS_NOT_CONFIRMED: "Teams not confirmed",
    FLAG_SINGLE_BOOK_MARKET: "Single-book market",
    FLAG_EARLY_MARKET: "Early market",
    FLAG_LARGE_BOOKMAKER_DISAGREEMENT: "Large bookmaker disagreement",
    FLAG_MODEL_WEAKER_IN_REGION: "Model historically weaker in this region",
    FLAG_ELITE_PLAYER_CONSERVATIVE_MODEL: "Elite-player conservative-model warning",
    FLAG_RARE_EVENT_THRESHOLD: "Rare-event threshold",
    FLAG_HIGH_TOG_VOLATILITY: "High recent TOG volatility",
    FLAG_RECENT_FORM_DISAGREES: "Recent form strongly disagrees",
}

# Calibration gap (|avg_predicted - actual_rate|) at or above this, in a
# band with enough sample to trust, counts as "weaker in this region".
_CALIBRATION_GAP_THRESHOLD = 0.08
# A same-book-devigged/raw consensus spread at or above this (in raw
# probability points) counts as a genuine market split, distinct from one
# outlier bookmaker (see consensus_and_outliers.detect_outlier_bookmaker,
# which is checked first and takes priority when it fires).
_CONSENSUS_SPREAD_THRESHOLD = 0.05
# Standard deviation of the last 5 games' time-on-ground%, in percentage
# points, at or above this counts as volatile.
_TOG_VOLATILITY_THRESHOLD = 12.0
_ELITE_DISPOSAL_BUCKETS_WITH_CONSERVATIVE_BIAS = {"elite_28_plus", "high_22_to_28"}


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance**0.5


def high_tog_volatility(db: Session, player_id: int, before) -> bool:
    form = load_recent_form(db, player_id, before, "time_on_ground_pct", n=5)
    sd = _stdev([float(v) for v in form.last5])
    return sd is not None and sd >= _TOG_VOLATILITY_THRESHOLD


@dataclass(frozen=True)
class UncertaintyFlagInputs:
    """Every optional signal this function can act on - callers pass
    whatever they've already computed for this opportunity (all of it is
    computed once per opportunity elsewhere in this stage, never
    recomputed here)."""

    any_confirmed_player_lineups: bool
    calibration_band: object | None = None  # similar_situation_calibration.CalibrationBand
    elite_disposal_bucket: str | None = None  # this player's bucket, if they're an evaluated disposal player
    outlier_check: object | None = None  # consensus_and_outliers.OutlierCheck
    consensus_spread: float | None = None
    tog_volatile: bool | None = None


def compute_uncertainty_flags(opportunity: dict, inputs: UncertaintyFlagInputs) -> list[str]:
    flags: list[str] = []

    if opportunity["opportunity_type"] == "team" and not inputs.any_confirmed_player_lineups:
        flags.append(FLAG_TEAMS_NOT_CONFIRMED)

    if opportunity.get("n_bookmakers", 0) <= 1:
        flags.append(FLAG_SINGLE_BOOK_MARKET)

    maturity = opportunity.get("market_maturity")
    if maturity is not None and maturity["tier"] == "early_market":
        flags.append(FLAG_EARLY_MARKET)

    if inputs.outlier_check is not None and inputs.outlier_check.is_outlier:
        flags.append(FLAG_LARGE_BOOKMAKER_DISAGREEMENT)
    elif inputs.consensus_spread is not None and inputs.consensus_spread >= _CONSENSUS_SPREAD_THRESHOLD:
        flags.append(FLAG_LARGE_BOOKMAKER_DISAGREEMENT)

    band = inputs.calibration_band
    if band is not None and band.meets_min_sample and band.avg_predicted is not None and band.actual_rate is not None:
        if abs(band.avg_predicted - band.actual_rate) >= _CALIBRATION_GAP_THRESHOLD:
            flags.append(FLAG_MODEL_WEAKER_IN_REGION)

    if opportunity["market_type"] == "player_disposals" and inputs.elite_disposal_bucket in _ELITE_DISPOSAL_BUCKETS_WITH_CONSERVATIVE_BIAS:
        flags.append(FLAG_ELITE_PLAYER_CONSERVATIVE_MODEL)

    if any("Rare-event" in w for w in opportunity.get("warnings", [])):
        flags.append(FLAG_RARE_EVENT_THRESHOLD)

    if inputs.tog_volatile:
        flags.append(FLAG_HIGH_TOG_VOLATILITY)

    recent_form = opportunity.get("recent_form")
    if recent_form is not None and recent_form.get("form_disagreement_label"):
        flags.append(FLAG_RECENT_FORM_DISAGREES)

    return flags


def uncertainty_flag_labels(flags: list[str]) -> list[str]:
    return [FLAG_LABELS[f] for f in flags if f in FLAG_LABELS]
