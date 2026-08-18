"""Defensive sanity checks (Section 17) run immediately before an
opportunity is allowed into a diversified view. Everything checked here
should, in practice, already be guaranteed by the pipelines this module
sits downstream of (prop_insights_normalized.load_normalized_prop_insights,
edges/calculator.py) — this is a final, explicit, visible checkpoint per
the brief's own requirement for one, not a claim that failures are common.
Every rejection carries its reason so it can be reported rather than
silently vanishing.
"""

# Disposal/goal thresholds far outside these ranges are outside anything
# the underlying models were ever evaluated against (see
# disposal_analysis.py's/goal_analysis.py's real threshold sets) — a
# genuinely modelled line should never fall outside this, so a hit here
# indicates a real upstream data problem worth surfacing, not a normal
# edge case to quietly tolerate.
_DISPOSAL_THRESHOLD_RANGE = (5.0, 45.0)
_GOAL_THRESHOLD_RANGE = (0.5, 8.0)


def sanity_check(opportunity: dict) -> str | None:
    """Returns None if the opportunity passes every check, else a short
    human-readable reason for exclusion."""
    if opportunity["best_price"] <= 1.0:
        return "offered odds not greater than 1"
    if not (0.0 < opportunity["model_probability"] < 1.0):
        return "model probability outside the valid (0, 1) range"
    if opportunity["match_id"] is None:
        return "match not resolved"

    if opportunity["opportunity_type"] == "player":
        if opportunity["player_id"] is None:
            return "player identity not resolved"
        if opportunity["selection_status"] == "confirmed_out":
            return "player is confirmed out"
        threshold = opportunity["threshold"]
        lo, hi = _DISPOSAL_THRESHOLD_RANGE if opportunity["market_type"] == "player_disposals" else _GOAL_THRESHOLD_RANGE
        if threshold is None or not (lo <= threshold <= hi):
            return "threshold is outside the historically modelled range"
    else:
        # Section 22: never let a manually-entered (often stale, test, or
        # historical) team-market quote headline a diversified view as if
        # it were a current live automated price. The raw All Markets view
        # (best_opportunities.load_best_opportunities) is unaffected — this
        # gate only applies downstream, to diversified/Best Overall.
        if opportunity.get("quote_source") == "manual":
            return "team odds are from manual entry, not a current live automated price"

    return None


def filter_sane(opportunities: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Returns (passed, [(rejected_opportunity, reason), ...])."""
    passed: list[dict] = []
    rejected: list[tuple[dict, str]] = []
    for o in opportunities:
        reason = sanity_check(o)
        if reason is None:
            passed.append(o)
        else:
            rejected.append((o, reason))
    return passed, rejected
