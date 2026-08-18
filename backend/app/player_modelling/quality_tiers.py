"""Quality/readiness tiers (Market Integrity + Final Weekly Picks stage,
Section 9) — an interpretable readiness label, SEPARATE from model
confidence (confidence_tier describes the MODEL's certainty about its own
projection; a quality tier describes whether THIS specific opportunity is
currently trustworthy enough to act on, combining model confidence with
price freshness, lineup status, and market integrity). Never "safe",
"lock", or "guaranteed" — this app makes no promises about outcomes.

Four tiers, in descending order of readiness:
- Strong candidate: meaningful model-market difference, fresh price,
  supported (non-extreme) threshold, sufficient model history, lineup
  confirmed where this is a player market, and price-integrity checks
  passed.
- Worth reviewing: a positive model-market difference exists, but with
  one meaningful caveat (aging price, lower confidence, single bookmaker,
  unconfirmed lineup, ...).
- Speculative: lower confidence, a rare-event threshold, thin bookmaker
  coverage, or lineup uncertainty makes this hard to act on with
  conviction, even though nothing here is disqualifying.
- Do not headline: stale, unresolved market/team, insufficient model
  history, a failed price-integrity check, or a confirmed-out player —
  hard-gated, never shown as a headline opportunity regardless of how
  attractive its raw numbers look.
"""

from dataclasses import dataclass

TIER_STRONG_CANDIDATE = "strong_candidate"
TIER_WORTH_REVIEWING = "worth_reviewing"
TIER_SPECULATIVE = "speculative"
TIER_DO_NOT_HEADLINE = "do_not_headline"

TIER_LABELS = {
    TIER_STRONG_CANDIDATE: "Strong candidate",
    TIER_WORTH_REVIEWING: "Worth reviewing",
    TIER_SPECULATIVE: "Speculative",
    TIER_DO_NOT_HEADLINE: "Do not headline",
}

TIER_ORDER = [TIER_STRONG_CANDIDATE, TIER_WORTH_REVIEWING, TIER_SPECULATIVE, TIER_DO_NOT_HEADLINE]

_MEANINGFUL_DIFFERENCE_EDGE_CATEGORIES = {"moderate_difference", "larger_difference"}
_HIGHER_OR_MODERATE_CONFIDENCE = {"higher_confidence", "moderate_confidence"}


@dataclass(frozen=True)
class QualityTierResult:
    tier: str
    label: str
    caveats: list[str]


def compute_quality_tier(opportunity: dict) -> QualityTierResult:
    caveats: list[str] = []

    # --- hard gates: always "Do not headline", regardless of raw numbers ---
    if opportunity["odds_freshness"] == "stale":
        return QualityTierResult(TIER_DO_NOT_HEADLINE, TIER_LABELS[TIER_DO_NOT_HEADLINE], ["The best available price is stale."])
    if opportunity["confidence_tier"] == "insufficient_history":
        return QualityTierResult(
            TIER_DO_NOT_HEADLINE, TIER_LABELS[TIER_DO_NOT_HEADLINE], ["Insufficient model history for this market."]
        )
    if opportunity["opportunity_type"] == "player" and opportunity.get("selection_status") == "confirmed_out":
        return QualityTierResult(TIER_DO_NOT_HEADLINE, TIER_LABELS[TIER_DO_NOT_HEADLINE], ["Player is confirmed out."])
    if not opportunity.get("eligible_price_available", True):
        return QualityTierResult(
            TIER_DO_NOT_HEADLINE, TIER_LABELS[TIER_DO_NOT_HEADLINE], ["No enabled bookmaker currently quotes this market."]
        )
    diag = opportunity.get("price_integrity")
    if diag is not None and not diag["passes_integrity"]:
        return QualityTierResult(
            TIER_DO_NOT_HEADLINE, TIER_LABELS[TIER_DO_NOT_HEADLINE], ["Price-integrity check failed: " + " ".join(diag["issues"])]
        )

    # --- soft signals: build up caveats, then rank into the remaining 3 tiers ---
    meaningful_difference = opportunity.get("edge_category") in _MEANINGFUL_DIFFERENCE_EDGE_CATEGORIES
    positive_difference = opportunity["difference_pp"] > 0
    fresh = opportunity["odds_freshness"] == "fresh"
    confident = opportunity["confidence_tier"] in _HIGHER_OR_MODERATE_CONFIDENCE
    lineup_confirmed = opportunity["opportunity_type"] != "player" or bool(opportunity.get("is_confirmed"))
    single_book = opportunity.get("n_bookmakers", 0) <= 1

    if not lineup_confirmed:
        caveats.append("Player's participation is not confirmed.")
    if not fresh:
        caveats.append("The best available price is aging, not fresh.")
    if opportunity["confidence_tier"] == "lower_confidence":
        caveats.append("Lower model confidence.")
    if single_book:
        caveats.append("Only one bookmaker currently quotes this market.")
    if any("Rare-event" in w for w in opportunity.get("warnings", [])):
        caveats.append("Rare-event threshold — smaller historical evaluation sample.")
    if opportunity.get("best_price_all_differs_from_enabled"):
        caveats.append("A better price exists at a bookmaker that isn't enabled in your settings.")

    if meaningful_difference and fresh and confident and lineup_confirmed and not single_book:
        tier = TIER_STRONG_CANDIDATE
    elif positive_difference and (fresh or confident):
        # Section 9: "Worth reviewing" = a positive difference plus ONE
        # meaningful caveat - an unconfirmed lineup is exactly that kind
        # of caveat (already appended above), not a reason to fall all
        # the way to "Speculative". Only "Strong candidate" requires a
        # confirmed lineup outright.
        tier = TIER_WORTH_REVIEWING
    else:
        tier = TIER_SPECULATIVE

    return QualityTierResult(tier=tier, label=TIER_LABELS[tier], caveats=caveats)


def quality_tier_as_dict(result: QualityTierResult) -> dict:
    return {"tier": result.tier, "label": result.label, "caveats": result.caveats}
