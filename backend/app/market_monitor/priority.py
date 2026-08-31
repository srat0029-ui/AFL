"""Transparent, rule-based case prioritisation (Alert Precision + Trader
Prioritisation stage, item 3). Every component below is a plain, named,
documented function of already-computed evidence (Alert.magnitude, Alert.
bookmaker_prices, a case's alert-type mix, an optional persistence record,
and the match's own kickoff time) — never a fitted/learned score, and
never tuned against outcomes (same boundary as every threshold in
app/market_monitor/detector.py).

Design: each component is scored 0.0-1.0 against a documented normalisation
constant, multiplied by a documented WEIGHT, and summed into a single
`total_score` (roughly 0-100, but not a hard ceiling — a case with many
kinds of corroborating evidence can legitimately exceed 100). A component
that has no applicable evidence in this case (e.g. no divergence alert
present) simply contributes 0, which is why deduplicating alerts into
cases (case_builder.py) directly matters for prioritisation: a case with
several alert TYPES agreeing scores higher than any one of them alone.
"""

import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

from app.edges.overround import implied_probability
from app.market_monitor.case_builder import AnomalyCase
from app.market_monitor.types import (
    ADJACENT_THRESHOLD_JUMP,
    BOOKMAKER_VS_CONSENSUS_OUTLIER,
    LARGE_MARKET_DISPERSION,
    MODEL_VS_MARKET_DIVERGENCE,
    NON_MONOTONIC_PLAYER_PRICE_CURVE,
    STALE_AFTER_CONTEXT_CHANGE,
    STALE_AFTER_LINEUP_CHANGE,
)

# --- Tier cutoffs (applied to total_score) — plain documented numbers,
# calibrated once against the real 2,653-alert dataset (see
# scripts/market_monitor_case_verification.py) to land on "tens of cases,
# not hundreds" per item 12's own target, never re-tuned against outcomes. ---
CRITICAL_CUTOFF = 55.0
HIGH_PRIORITY_CUTOFF = 50.0
REVIEW_WORTHY_CUTOFF = 22.0

TIER_CRITICAL = "critical"
TIER_HIGH_PRIORITY = "high_priority"
TIER_REVIEW_WORTHY = "review_worthy"
TIER_RAW_DETECTION = "raw_detection"  # below REVIEW_WORTHY_CUTOFF — preserved (item 1) but not surfaced by default

# --- Normalisation constants (the value at which a component maxes out at
# 1.0) and weights (how much a maxed-out component contributes) ---
NORM_DIVERGENCE_PP = 0.30
NORM_OUTLIER_PCT = 60.0
NORM_DISPERSION_PP = 0.30
NORM_CROSS_BOOK_COUNT = 8.0
PERSISTENCE_NORM_SNAPSHOTS = 5
PROXIMITY_NORM_HOURS = 72.0
CURVE_JUMP_RATIO_NORM = 10.0
CLUSTER_TOLERANCE_PP = 0.03  # a book within 3pp of the median counts as "clustered"

WEIGHT_DIVERGENCE = 20.0
WEIGHT_OUTLIER = 12.0
WEIGHT_DISPERSION = 6.0
WEIGHT_CROSS_BOOK = 10.0
WEIGHT_CLUSTER_TIGHTNESS = 6.0
WEIGHT_FRESHNESS = 8.0
WEIGHT_CONTEXT = 12.0
WEIGHT_MODEL_SUPPORT_BONUS = 10.0
WEIGHT_PERSISTENCE = 10.0
WEIGHT_CURVE_CONFIRMATION = 14.0
WEIGHT_PROXIMITY = 6.0

FRESHNESS_SCORE = {"fresh": 1.0, "aging": 0.5, "stale": 0.15}

# item 7's own examples, most to least severe — a lineup event NOT in this
# map (i.e. routine roster churn) never reaches check_lineup_staleness at
# all (see context_staleness.NOTABLE_SELECTION_STATUSES), so every key
# here is already a "notable" event; this just orders them by trading-desk
# impact. Manual MatchContextItem entries (team news/injury notes) get a
# single flat, lower weight — "minor/manual informational context should
# rank lower" (item 7) — since they're free-text and not this
# structurally-typed.
LINEUP_EVENT_SEVERITY = {
    "confirmed_out": 1.0,
    "substitute": 0.85,
    "emergency": 0.7,
    "confirmed_selected": 0.6,  # "team announcement"
    "uncertain": 0.4,
}
MANUAL_CONTEXT_SEVERITY = 0.35


@dataclass(frozen=True)
class PriorityComponent:
    name: str
    raw_value: float | None  # the underlying evidence number, for display
    normalized: float  # 0.0-1.0
    weight: float
    contribution: float  # normalized * weight
    explanation: str


@dataclass(frozen=True)
class PriorityBreakdown:
    total_score: float
    tier: str
    components: list[PriorityComponent]
    persistence_label: str  # "transient" | "persistent"
    n_snapshots: int
    model_support: bool | None  # None when not applicable (no outlier evidence to check)


def _max_magnitude(case: AnomalyCase, alert_type: str) -> float | None:
    values = [a.magnitude for a in case.alerts if a.alert_type == alert_type and a.magnitude is not None]
    return max(values) if values else None


def _latest_prices_by_bookmaker(case: AnomalyCase) -> dict[str, float]:
    """Latest price per bookmaker across every alert's evidence in this
    case — the same "one number per book" convention the consensus engine
    itself uses (see app/market_monitor/common.py)."""
    latest: dict[str, tuple[float, datetime]] = {}
    for a in case.alerts:
        for b in a.bookmaker_prices:
            cur = latest.get(b.bookmaker_name)
            if cur is None or b.recorded_at > cur[1]:
                latest[b.bookmaker_name] = (b.price_decimal, b.recorded_at)
    return {name: price for name, (price, _) in latest.items()}


def _cluster_tightness(prices_by_book: dict[str, float]) -> tuple[float, int]:
    """Fraction of books within CLUSTER_TOLERANCE_PP of the median implied
    probability (item 5's "percentage of books clustered together"), plus
    the raw book count."""
    if len(prices_by_book) < 2:
        return 1.0, len(prices_by_book)
    probs = [implied_probability(p) for p in prices_by_book.values()]
    median = statistics.median(probs)
    clustered = sum(1 for p in probs if abs(p - median) <= CLUSTER_TOLERANCE_PP)
    return clustered / len(probs), len(probs)


def _model_support(case: AnomalyCase, prices_by_book: dict[str, float]) -> bool | None:
    """item 6: is a market-side anomaly independently corroborated by the
    model, or is it a market-only disagreement? Finds the book whose
    implied probability sits FARTHEST from the group's own consensus (the
    natural "outlier candidate" among this case's evidence) and checks
    whether the model's own probability sits closer to that book than to
    the consensus — i.e. the model, derived independently of any
    bookmaker, happens to agree with the diverging book rather than the
    crowd. Never claims the crowd (or the outlier) is "wrong" — only
    reports whether two independent sources (model, one book) line up."""
    div = next((a for a in case.alerts if a.alert_type == MODEL_VS_MARKET_DIVERGENCE), None)
    if div is None or div.model_probability is None or div.market_consensus_probability is None or len(prices_by_book) < 2:
        return None
    probs = {name: implied_probability(p) for name, p in prices_by_book.items()}
    consensus = div.market_consensus_probability
    outlier_name = max(probs, key=lambda n: abs(probs[n] - consensus))
    outlier_prob = probs[outlier_name]
    return abs(div.model_probability - outlier_prob) < abs(div.model_probability - consensus)


def _context_component(case: AnomalyCase) -> tuple[float, str]:
    staleness_alerts = [a for a in case.alerts if a.alert_type in (STALE_AFTER_LINEUP_CHANGE, STALE_AFTER_CONTEXT_CHANGE)]
    if not staleness_alerts:
        return 0.0, "No context/lineup event postdates this market's latest quote."
    best = 0.0
    label = ""
    for a in staleness_alerts:
        if a.alert_type == STALE_AFTER_LINEUP_CHANGE and a.lineup_status in LINEUP_EVENT_SEVERITY:
            v = LINEUP_EVENT_SEVERITY[a.lineup_status]
            if v > best:
                best, label = v, f"lineup event ({a.lineup_status})"
        elif a.alert_type == STALE_AFTER_CONTEXT_CHANGE:
            if MANUAL_CONTEXT_SEVERITY > best:
                best, label = MANUAL_CONTEXT_SEVERITY, "manual context note"
    return best, (f"Quote predates a {label}." if label else "")


def _curve_component(case: AnomalyCase) -> tuple[float, str]:
    jump = next((a for a in case.alerts if a.alert_type == ADJACENT_THRESHOLD_JUMP), None)
    if jump is not None and jump.magnitude is not None:
        return min(jump.magnitude / CURVE_JUMP_RATIO_NORM, 1.0), f"Adjacent-threshold jump {jump.magnitude:.1f}x its neighbouring gaps (neighbours already confirmed normal — see item 8's design)."
    mono = next((a for a in case.alerts if a.alert_type == NON_MONOTONIC_PLAYER_PRICE_CURVE), None)
    if mono is not None:
        return 1.0, "Strict monotonicity violation — structurally invalid regardless of magnitude."
    return 0.0, ""


def compute_priority(case: AnomalyCase, *, kickoff: datetime | None, n_snapshots: int = 1, now: datetime | None = None) -> PriorityBreakdown:
    now = now or datetime.now(timezone.utc)
    components: list[PriorityComponent] = []

    div_pp = _max_magnitude(case, MODEL_VS_MARKET_DIVERGENCE)
    div_norm = min(div_pp / NORM_DIVERGENCE_PP, 1.0) if div_pp is not None else 0.0
    components.append(PriorityComponent("deviation_from_model", div_pp, div_norm, WEIGHT_DIVERGENCE, div_norm * WEIGHT_DIVERGENCE, "How far the model's own probability sits from market consensus." if div_pp is not None else "No model-vs-market divergence evidence in this case."))

    outlier_pct = _max_magnitude(case, BOOKMAKER_VS_CONSENSUS_OUTLIER)
    outlier_norm = min(outlier_pct / NORM_OUTLIER_PCT, 1.0) if outlier_pct is not None else 0.0
    components.append(PriorityComponent("bookmaker_outlier_size", outlier_pct, outlier_norm, WEIGHT_OUTLIER, outlier_norm * WEIGHT_OUTLIER, "How far the best price sits from the rest of the eligible market." if outlier_pct is not None else "No outlier-price evidence in this case."))

    disp_pp = _max_magnitude(case, LARGE_MARKET_DISPERSION)
    disp_norm = min(disp_pp / NORM_DISPERSION_PP, 1.0) if disp_pp is not None else 0.0
    components.append(PriorityComponent("market_dispersion", disp_pp, disp_norm, WEIGHT_DISPERSION, disp_norm * WEIGHT_DISPERSION, "How widely eligible-book prices are spread." if disp_pp is not None else "No dispersion evidence in this case."))

    context_norm, context_note = _context_component(case)
    components.append(PriorityComponent("context_severity", context_norm, context_norm, WEIGHT_CONTEXT, context_norm * WEIGHT_CONTEXT, context_note or "No context/lineup staleness evidence in this case."))

    curve_norm, curve_note = _curve_component(case)
    components.append(PriorityComponent("curve_confirmation", curve_norm, curve_norm, WEIGHT_CURVE_CONFIRMATION, curve_norm * WEIGHT_CURVE_CONFIRMATION, curve_note or "No pricing-curve evidence in this case."))

    # --- Amplifiers: item 5's "a bookmaker that differs from one other
    # bookmaker is weaker evidence than one that differs from 8-10
    # sportsbooks" only makes sense RELATIVE to there being a real
    # divergence/outlier/dispersion/curve/context signal in the first
    # place — many books, a fresh quote, or persistence across snapshots
    # cannot on their own manufacture an anomaly. Each amplifier's
    # contribution is scaled by `primary_strength` (the strongest direct
    # anomaly signal already found above), so a case with NO real primary
    # evidence stays near zero regardless of how many books or how fresh
    # the price is, and a case WITH strong primary evidence gets a
    # meaningfully bigger boost from strong corroboration than weak.
    primary_strength = max(div_norm, outlier_norm, disp_norm, curve_norm, context_norm)

    prices_by_book = _latest_prices_by_bookmaker(case)
    n_books = len(prices_by_book)
    cross_book_norm = min(n_books / NORM_CROSS_BOOK_COUNT, 1.0)
    components.append(PriorityComponent("cross_book_confirmation", float(n_books), cross_book_norm, WEIGHT_CROSS_BOOK, cross_book_norm * WEIGHT_CROSS_BOOK * primary_strength, f"{n_books} eligible bookmaker(s) contributed evidence — more books agreeing/disagreeing is stronger signal than one lone quote (scaled by how strong the underlying finding already is)."))

    cluster_frac, cluster_n = _cluster_tightness(prices_by_book)
    cluster_val = cluster_frac if cluster_n >= 2 else 0.0
    components.append(PriorityComponent("cluster_tightness", cluster_frac if cluster_n >= 2 else None, cluster_val, WEIGHT_CLUSTER_TIGHTNESS, cluster_val * WEIGHT_CLUSTER_TIGHTNESS * primary_strength, f"{cluster_frac:.0%} of books cluster within {CLUSTER_TOLERANCE_PP*100:.0f}pp of the median — a tight cluster makes a lone outlier's divergence more credible." if cluster_n >= 2 else "Fewer than 2 books — no cluster to measure."))

    freshness = FRESHNESS_SCORE.get(case.primary_alert.freshness, 0.5)
    components.append(PriorityComponent("quote_freshness", None, freshness, WEIGHT_FRESHNESS, freshness * WEIGHT_FRESHNESS * primary_strength, f"Latest relevant quote is {case.primary_alert.freshness or 'of unknown freshness'}."))

    model_support = _model_support(case, prices_by_book)
    support_contribution = WEIGHT_MODEL_SUPPORT_BONUS * primary_strength if model_support else 0.0
    components.append(PriorityComponent("model_supported_outlier", None, 1.0 if model_support else 0.0, WEIGHT_MODEL_SUPPORT_BONUS, support_contribution, "The model's own (independent) probability agrees with the diverging book, not the consensus — stronger QA evidence." if model_support else ("The model agrees with consensus, not the diverging book — a market-only disagreement." if model_support is False else "No divergence evidence to compare model support against.")))

    persistence_norm = min(max(n_snapshots - 1, 0) / PERSISTENCE_NORM_SNAPSHOTS, 1.0)
    persistence_label = "persistent" if n_snapshots >= 2 else "transient"
    components.append(PriorityComponent("persistence", float(n_snapshots), persistence_norm, WEIGHT_PERSISTENCE, persistence_norm * WEIGHT_PERSISTENCE * primary_strength, f"Seen in {n_snapshots} detection pass(es) — {persistence_label}."))

    if kickoff is not None:
        hours_to_kickoff = max((kickoff - now).total_seconds() / 3600.0, 0.0)
        proximity_norm = max(0.0, 1.0 - hours_to_kickoff / PROXIMITY_NORM_HOURS)
    else:
        hours_to_kickoff, proximity_norm = None, 0.0
    components.append(PriorityComponent("proximity_to_kickoff", hours_to_kickoff, proximity_norm, WEIGHT_PROXIMITY, proximity_norm * WEIGHT_PROXIMITY * primary_strength, f"{hours_to_kickoff:.0f}h to kickoff." if hours_to_kickoff is not None else "Kickoff time unknown."))

    total = sum(c.contribution for c in components)
    if total >= CRITICAL_CUTOFF:
        tier = TIER_CRITICAL
    elif total >= HIGH_PRIORITY_CUTOFF:
        tier = TIER_HIGH_PRIORITY
    elif total >= REVIEW_WORTHY_CUTOFF:
        tier = TIER_REVIEW_WORTHY
    else:
        tier = TIER_RAW_DETECTION

    return PriorityBreakdown(total_score=total, tier=tier, components=components, persistence_label=persistence_label, n_snapshots=n_snapshots, model_support=model_support)
