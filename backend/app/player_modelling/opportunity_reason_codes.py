"""Structured reason codes (Section 18): every reason an opportunity
headlines is a named, checkable code computed from data already on the
opportunity dict — never free-form prose the codes themselves don't
support. Human-readable labels are a fixed lookup table, not generated.
"""

REASON_MODEL_ABOVE_MARKET = "MODEL_ABOVE_MARKET"
REASON_BEST_PRICE_CLEARLY_HIGHER = "BEST_PRICE_CLEARLY_HIGHER_THAN_OTHER_BOOKS"
REASON_HIGHER_CONFIDENCE = "HIGHER_CONFIDENCE"
REASON_WELL_CALIBRATED = "WELL_CALIBRATED_THRESHOLD"
REASON_CONFIRMED_LINEUP = "CONFIRMED_LINEUP"
REASON_SINGLE_BOOK_ONLY = "SINGLE_BOOK_ONLY"
REASON_RECENT_FORM_DISAGREES = "RECENT_FORM_DISAGREES"
REASON_RARE_EVENT_THRESHOLD = "RARE_EVENT_THRESHOLD"

REASON_LABELS: dict[str, str] = {
    REASON_MODEL_ABOVE_MARKET: "Model probability is above the market's price",
    REASON_BEST_PRICE_CLEARLY_HIGHER: "Best price is clearly higher than other bookmakers",
    REASON_HIGHER_CONFIDENCE: "Higher model confidence",
    REASON_WELL_CALIBRATED: "This threshold has strong historical calibration",
    REASON_CONFIRMED_LINEUP: "Player's selection is confirmed",
    REASON_SINGLE_BOOK_ONLY: "Only one bookmaker currently quotes this market",
    REASON_RECENT_FORM_DISAGREES: "Model and recent form disagree",
    REASON_RARE_EVENT_THRESHOLD: "This is a rare-event threshold — smaller historical sample",
}

# A best price at least this much better than the next-best bookmaker
# counts as "clearly higher" (vs. two books quoting essentially the same
# price, where headlining a bookmaker difference would overstate it).
_CLEARLY_HIGHER_RATIO = 1.05
_WELL_CALIBRATED_ECE = 0.02


def compute_reason_codes(opportunity: dict, *, form_disagreement: bool = False) -> list[str]:
    codes: list[str] = []

    if opportunity["difference_pp"] > 0:
        codes.append(REASON_MODEL_ABOVE_MARKET)

    prices = sorted(
        (b["price_decimal"] for b in opportunity["bookmakers"] if b.get("eligibility", "included") == "included"),
        reverse=True,
    )
    if len(prices) >= 2:
        if prices[0] >= prices[1] * _CLEARLY_HIGHER_RATIO:
            codes.append(REASON_BEST_PRICE_CLEARLY_HIGHER)
    else:
        codes.append(REASON_SINGLE_BOOK_ONLY)

    if opportunity["confidence_tier"] == "higher_confidence":
        codes.append(REASON_HIGHER_CONFIDENCE)

    calibration = opportunity.get("calibration")
    if calibration is not None and calibration["ece"] < _WELL_CALIBRATED_ECE:
        codes.append(REASON_WELL_CALIBRATED)

    if opportunity.get("is_confirmed"):
        codes.append(REASON_CONFIRMED_LINEUP)

    if any("Rare-event" in w for w in opportunity["warnings"]):
        codes.append(REASON_RARE_EVENT_THRESHOLD)

    if form_disagreement:
        codes.append(REASON_RECENT_FORM_DISAGREES)

    return codes


def reason_labels(codes: list[str]) -> list[str]:
    return [REASON_LABELS[c] for c in codes if c in REASON_LABELS]
