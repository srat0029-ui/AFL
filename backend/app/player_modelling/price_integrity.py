"""Extreme price-difference diagnostic (Market Integrity + Final Weekly
Picks stage, Section 2). The exchange-price fix in bookmaker_classification.py
already resolves the single biggest known cause of an inflated
price-shopping gap (the real "Richmond to win" case — see
best_opportunities.py). This module is the general-purpose safety net for
every OTHER way two ELIGIBLE (sportsbook, enabled) bookmaker prices for
the exact same market could legitimately or spuriously diverge a lot:
one quote being stale while the other is fresh, a large gap between when
each was recorded, or (structurally, by construction of the exact-tuple
grouping keys in best_opportunities.py/prop_insights_normalized.py) a
genuine, large, live disagreement between two current sportsbook prices.

Nothing here suppresses a flagged market — Section 2 is explicit that a
large discrepancy "should not headline... until it passes these checks",
not that it should be hidden. Diagnostics attach machine-checkable facts
and a pass/fail summary; the Final Weekly Shortlist (final_shortlist.py)
is what actually declines to headline a market that fails.
"""

from dataclasses import dataclass
from datetime import timedelta

from app.models.bookmaker import ELIGIBILITY_INCLUDED

# Diagnostic bands (Section 2's example thresholds — diagnostic, not proof
# of error). Sorted descending so the FIRST match is the tightest band a
# given price_advantage_pct clears.
DIAGNOSTIC_BANDS_PCT: tuple[float, ...] = (50.0, 30.0, 15.0)

# Two "current" sportsbook prices for the exact same market recorded more
# than this far apart are being compared across different moments in time,
# not genuinely against each other right now.
_MAX_RECORDED_AT_GAP = timedelta(hours=6)


@dataclass(frozen=True)
class PriceSpreadDiagnostic:
    price_advantage_pct: float
    band_pct: float  # the tightest DIAGNOSTIC_BANDS_PCT threshold cleared
    best_bookmaker: str
    best_price: float
    best_price_freshness: str
    next_best_bookmaker: str
    next_best_price: float
    next_best_price_freshness: str
    recorded_at_gap_seconds: float
    passes_integrity: bool
    checks: dict[str, bool]
    issues: list[str]


def diagnose_price_spread(opportunity: dict) -> PriceSpreadDiagnostic | None:
    """Returns None if fewer than 2 eligible bookmakers quote this market,
    or if the eligible-only price advantage doesn't clear the loosest
    diagnostic band. `opportunity["bookmakers"]` must already be annotated
    with is_exchange/eligibility (see bookmaker_classification.py) —
    exchange/excluded bookmakers are excluded from this comparison
    entirely, exactly like price_advantage_pct."""
    eligible = [b for b in opportunity["bookmakers"] if b.get("eligibility", ELIGIBILITY_INCLUDED) == ELIGIBILITY_INCLUDED]
    if len(eligible) < 2:
        return None

    ranked = sorted(eligible, key=lambda b: b["price_decimal"], reverse=True)
    best, next_best = ranked[0], ranked[1]
    if next_best["price_decimal"] <= 0:
        return None
    pct = (best["price_decimal"] - next_best["price_decimal"]) / next_best["price_decimal"] * 100.0

    band = next((t for t in DIAGNOSTIC_BANDS_PCT if pct >= t), None)
    if band is None:
        return None

    gap_seconds = abs((best["recorded_at"] - next_best["recorded_at"]).total_seconds())

    checks = {
        # Structurally guaranteed by the exact-tuple grouping keys these
        # opportunities are built from (_TeamMarketKey / _MarketKey) —
        # recorded here explicitly per Section 2's own checklist, not
        # re-derived, since two prices in the same `bookmakers` list can
        # only exist if they already passed exact match+market+
        # selection+line/threshold equality upstream.
        "exact_market_match": True,
        "same_side_selection": True,
        "best_price_fresh": best["freshness"] != "stale",
        "next_best_price_fresh": next_best["freshness"] != "stale",
        "recorded_close_together": gap_seconds <= _MAX_RECORDED_AT_GAP.total_seconds(),
        "neither_price_is_exchange": not best.get("is_exchange", False) and not next_best.get("is_exchange", False),
    }

    issues = []
    if not checks["best_price_fresh"]:
        issues.append(f"{best['bookmaker_name']}'s price is stale.")
    if not checks["next_best_price_fresh"]:
        issues.append(f"{next_best['bookmaker_name']}'s price is stale.")
    if not checks["recorded_close_together"]:
        issues.append(f"The two prices were recorded {gap_seconds / 3600:.1f} hours apart — not a like-for-like current comparison.")
    if not checks["neither_price_is_exchange"]:
        issues.append("One of the two prices is from a betting exchange despite being marked eligible — check bookmaker eligibility settings.")

    return PriceSpreadDiagnostic(
        price_advantage_pct=pct,
        band_pct=band,
        best_bookmaker=best["bookmaker_name"],
        best_price=best["price_decimal"],
        best_price_freshness=best["freshness"],
        next_best_bookmaker=next_best["bookmaker_name"],
        next_best_price=next_best["price_decimal"],
        next_best_price_freshness=next_best["freshness"],
        recorded_at_gap_seconds=gap_seconds,
        checks=checks,
        passes_integrity=all(checks.values()),
        issues=issues,
    )


def diagnostic_as_dict(diag: PriceSpreadDiagnostic) -> dict:
    return {
        "price_advantage_pct": diag.price_advantage_pct,
        "band_pct": diag.band_pct,
        "best_bookmaker": diag.best_bookmaker,
        "best_price": diag.best_price,
        "best_price_freshness": diag.best_price_freshness,
        "next_best_bookmaker": diag.next_best_bookmaker,
        "next_best_price": diag.next_best_price,
        "next_best_price_freshness": diag.next_best_price_freshness,
        "recorded_at_gap_seconds": diag.recorded_at_gap_seconds,
        "checks": diag.checks,
        "passes_integrity": diag.passes_integrity,
        "issues": diag.issues,
    }
