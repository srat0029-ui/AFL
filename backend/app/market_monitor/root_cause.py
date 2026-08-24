"""Root-cause intelligence (item 4): for a MODEL_VS_MARKET_DIVERGENCE case,
classify which broad category is most plausible using ONLY the structured
evidence already computed by the detector/case builder — no free-text
generation, no LLM narrative, purely rule-based and deterministic. A case
can plausibly fit more than one category; `plausible_causes` is a ranked
list, most-specific-and-well-evidenced first, with `most_plausible` the
single best guess for a compact summary view.
"""

from dataclasses import dataclass

from app.market_monitor.case_builder import AnomalyCase
from app.market_monitor.detector import DIVERGENCE_CRITICAL_PP
from app.market_monitor.types import (
    ADJACENT_THRESHOLD_JUMP,
    BOOKMAKER_VS_CONSENSUS_OUTLIER,
    LARGE_MARKET_DISPERSION,
    NON_MONOTONIC_PLAYER_PRICE_CURVE,
    STALE_AFTER_CONTEXT_CHANGE,
    STALE_AFTER_LINEUP_CHANGE,
)

SINGLE_BOOK_OUTLIER = "single_book_outlier"
STALE_QUOTE = "stale_quote"
RECENT_CONTEXT_CHANGE = "recent_context_change"
MODEL_RISK_FLAG_PRESENT = "model_risk_flag_present"
THRESHOLD_SPECIFIC_ANOMALY = "threshold_specific_anomaly"
MODEL_VS_BROAD_MARKET_DISAGREEMENT = "model_vs_broad_market_disagreement"
POTENTIAL_MODEL_LIMITATION = "potential_model_limitation"

_LABELS = {
    SINGLE_BOOK_OUTLIER: "One bookmaker's price is materially out of line with the rest of the eligible market.",
    STALE_QUOTE: "The relevant quote(s) are aging or stale.",
    RECENT_CONTEXT_CHANGE: "A lineup or team-news event postdates the latest quote.",
    MODEL_RISK_FLAG_PRESENT: "This player already carries a usage-regime-change risk flag.",
    THRESHOLD_SPECIFIC_ANOMALY: "The pricing curve shows a non-monotonic or unusually large jump at/near this threshold.",
    MODEL_VS_BROAD_MARKET_DISAGREEMENT: "The eligible market is tightly clustered (low dispersion, several books agree) and still disagrees with the model — a genuine model-vs-market gap, not noise from any single book.",
    POTENTIAL_MODEL_LIMITATION: "Broad, tightly-clustered market disagreement that persists across snapshots — the most plausible explanation shifts toward the model itself, not the market.",
}

DISPERSION_TIGHT_PP = 0.08  # below this, the eligible market is "tightly clustered" (see priority.py's own CLUSTER_TOLERANCE_PP for the same idea at the per-book level)

# Genuine Prospective Operation stage, item 8: the "Matthew Kennedy" research
# pattern - a single-book outlier is present, but the model-vs-market gap
# survives even after excluding that outlier's price. Kept as its own tag
# (rather than folded into SINGLE_BOOK_OUTLIER) so it can be tracked
# separately across future live cases: does the residual gap persist or
# converge once the outlier itself resolves.
RESEARCH_CATEGORY_OUTLIER_WITH_RESIDUAL_DIVERGENCE = "single_book_outlier_residual_disagreement"


def compute_research_category(model_probability: float | None, outlier) -> str | None:
    """outlier: app.player_modelling.consensus_and_outliers.OutlierCheck | None.
    Reuses outlier.median_eligible_price (the consensus of the OTHER books,
    already computed by the same outlier check the detector itself uses) and
    the detector's own DIVERGENCE_CRITICAL_PP - no new threshold invented."""
    if outlier is None or not outlier.is_outlier or model_probability is None:
        return None
    residual_market_probability = 1.0 / outlier.median_eligible_price
    if abs(residual_market_probability - model_probability) >= DIVERGENCE_CRITICAL_PP:
        return RESEARCH_CATEGORY_OUTLIER_WITH_RESIDUAL_DIVERGENCE
    return None


@dataclass(frozen=True)
class RootCauseFinding:
    most_plausible: str
    label: str
    plausible_causes: list[str]
    evidence: list[str]


def diagnose(case: AnomalyCase, *, n_snapshots: int = 1) -> RootCauseFinding:
    causes: list[str] = []
    evidence: list[str] = []
    types = {case.primary_alert.alert_type} | set(case.supporting_alert_types)

    if BOOKMAKER_VS_CONSENSUS_OUTLIER in types:
        causes.append(SINGLE_BOOK_OUTLIER)
        outlier_alert = next((a for a in case.alerts if a.alert_type == BOOKMAKER_VS_CONSENSUS_OUTLIER), None)
        if outlier_alert:
            evidence.append(outlier_alert.detail)

    if case.primary_alert.freshness in ("stale", "aging"):
        causes.append(STALE_QUOTE)
        evidence.append(f"Freshness: {case.primary_alert.freshness}.")

    if STALE_AFTER_LINEUP_CHANGE in types or STALE_AFTER_CONTEXT_CHANGE in types:
        causes.append(RECENT_CONTEXT_CHANGE)
        ctx_alert = next((a for a in case.alerts if a.alert_type in (STALE_AFTER_LINEUP_CHANGE, STALE_AFTER_CONTEXT_CHANGE)), None)
        if ctx_alert:
            evidence.append(ctx_alert.detail)

    if case.primary_alert.model_risk_flags:
        causes.append(MODEL_RISK_FLAG_PRESENT)
        evidence.append("; ".join(f.description for f in case.primary_alert.model_risk_flags))

    if ADJACENT_THRESHOLD_JUMP in types or NON_MONOTONIC_PLAYER_PRICE_CURVE in types:
        causes.append(THRESHOLD_SPECIFIC_ANOMALY)
        curve_alert = next((a for a in case.alerts if a.alert_type in (ADJACENT_THRESHOLD_JUMP, NON_MONOTONIC_PLAYER_PRICE_CURVE)), None)
        if curve_alert:
            evidence.append(curve_alert.detail)

    dispersion_alert = next((a for a in case.alerts if a.alert_type == LARGE_MARKET_DISPERSION), None)
    is_tight = dispersion_alert is None or (dispersion_alert.magnitude is not None and dispersion_alert.magnitude < DISPERSION_TIGHT_PP)
    n_books = len({b.bookmaker_name for a in case.alerts for b in a.bookmaker_prices})
    if is_tight and n_books >= 3 and not causes:
        # No single-book, staleness, context, or curve explanation fits, and
        # the market is tightly agreed among several books - the broadest,
        # least-explained-away category.
        if n_snapshots >= 3:
            causes.append(POTENTIAL_MODEL_LIMITATION)
            evidence.append(f"{n_books} books tightly clustered, persisted across {n_snapshots} detection passes, no other explanation fits.")
        else:
            causes.append(MODEL_VS_BROAD_MARKET_DISAGREEMENT)
            evidence.append(f"{n_books} books tightly clustered (dispersion below {DISPERSION_TIGHT_PP*100:.0f}pp where measured), no single-book/staleness/context/curve explanation fits.")

    if not causes:
        causes.append(MODEL_VS_BROAD_MARKET_DISAGREEMENT)
        evidence.append("No specific corroborating factor identified from existing structured evidence.")

    most_plausible = causes[0]
    return RootCauseFinding(most_plausible=most_plausible, label=_LABELS[most_plausible], plausible_causes=causes, evidence=evidence)
