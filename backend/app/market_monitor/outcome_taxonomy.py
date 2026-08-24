"""Case outcome taxonomy (Prospective Alert Validation stage, item 3) —
8 codes, deterministic, rule-based, reusing the EXACT same movement/
divergence thresholds already validated in app/market_monitor/movement.py
and detector.py (never new thresholds — this stage adds no new anomaly
detection, only classifies what happened to cases already frozen). A case
may carry more than one code. Never labelled a "win" — these describe
MARKET/MODEL BEHAVIOUR, not a betting outcome.
"""

from app.market_monitor.movement import SHARP_MOVE_PP, STABLE_MOVE_PP

MARKET_MOVED_TOWARD_MODEL = "MARKET_MOVED_TOWARD_MODEL"
MARKET_MOVED_AWAY_FROM_MODEL = "MARKET_MOVED_AWAY_FROM_MODEL"
OUTLIER_CONVERGED = "OUTLIER_CONVERGED"
CONSENSUS_REPRICED_AFTER_CONTEXT = "CONSENSUS_REPRICED_AFTER_CONTEXT"
CURVE_ANOMALY_RESOLVED = "CURVE_ANOMALY_RESOLVED"
PERSISTED_TO_KICKOFF = "PERSISTED_TO_KICKOFF"
MODEL_MOVED_TOWARD_MARKET = "MODEL_MOVED_TOWARD_MARKET"
INCONCLUSIVE = "INCONCLUSIVE"

ALL_OUTCOME_CODES: tuple[str, ...] = (
    MARKET_MOVED_TOWARD_MODEL, MARKET_MOVED_AWAY_FROM_MODEL, OUTLIER_CONVERGED, CONSENSUS_REPRICED_AFTER_CONTEXT,
    CURVE_ANOMALY_RESOLVED, PERSISTED_TO_KICKOFF, MODEL_MOVED_TOWARD_MARKET, INCONCLUSIVE,
)


def classify_outcomes(
    *,
    consensus_at_freeze: float | None,
    consensus_at_settlement: float | None,
    model_probability_at_freeze: float | None,
    model_probability_at_settlement: float | None,
    had_outlier_alert: bool,
    outlier_converged: bool | None,
    had_stale_context_alert: bool,
    stale_market_repriced: bool | None,
    had_curve_alert: bool,
    curve_anomaly_resolved: bool | None,
) -> list[str]:
    codes: list[str] = []

    if consensus_at_freeze is not None and consensus_at_settlement is not None and model_probability_at_freeze is not None:
        consensus_move = consensus_at_settlement - consensus_at_freeze
        gap_at_freeze = abs(consensus_at_freeze - model_probability_at_freeze)
        gap_at_settlement = abs(consensus_at_settlement - model_probability_at_freeze)
        if abs(consensus_move) >= STABLE_MOVE_PP:
            if gap_at_settlement < gap_at_freeze:
                codes.append(MARKET_MOVED_TOWARD_MODEL)
            elif gap_at_settlement > gap_at_freeze:
                codes.append(MARKET_MOVED_AWAY_FROM_MODEL)

    if had_outlier_alert and outlier_converged:
        codes.append(OUTLIER_CONVERGED)

    if had_stale_context_alert and stale_market_repriced:
        codes.append(CONSENSUS_REPRICED_AFTER_CONTEXT)

    if had_curve_alert and curve_anomaly_resolved:
        codes.append(CURVE_ANOMALY_RESOLVED)

    if (
        model_probability_at_freeze is not None and model_probability_at_settlement is not None
        and consensus_at_freeze is not None
    ):
        model_move = model_probability_at_settlement - model_probability_at_freeze
        if abs(model_move) >= STABLE_MOVE_PP:
            gap_before = abs(model_probability_at_freeze - consensus_at_freeze)
            gap_after = abs(model_probability_at_settlement - consensus_at_freeze)
            if gap_after < gap_before:
                codes.append(MODEL_MOVED_TOWARD_MARKET)

    if consensus_at_freeze is not None and consensus_at_settlement is not None and abs(consensus_at_settlement - consensus_at_freeze) < SHARP_MOVE_PP:
        # Nothing about the primary market-vs-model relationship changed
        # materially, and none of the specific mechanisms above resolved —
        # the situation simply persisted, unbroken, through to kickoff.
        if MARKET_MOVED_TOWARD_MODEL not in codes and MARKET_MOVED_AWAY_FROM_MODEL not in codes:
            codes.append(PERSISTED_TO_KICKOFF)

    if not codes:
        codes.append(INCONCLUSIVE)

    return codes
