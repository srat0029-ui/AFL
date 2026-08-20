"""Three-tier Prop Insights UX (product-quality stage): "Best Opportunities
often shows zero" was traced to load_best_opportunities' own DEFAULT gates
(include_uncertain=False) hard-EXCLUDING any not-yet-confirmed player at
the SOURCE, before quality_tiers.compute_quality_tier ever runs — bypassing
that module's own, already-correct design where an unconfirmed lineup is
just ONE caveat that downgrades an opportunity to "worth_reviewing", not a
reason to hide it. Nothing here changes a model, a probability, or the
opportunity_score formula: this module only changes which of the ALREADY-
COMPUTED quality tiers get bucketed into which visible section.

Three tiers, each reusing the exact same opportunity_score/quality_tier
computed by best_opportunities.load_best_opportunities:
  - Best Opportunities:   quality_tier == strong_candidate, diversified.
  - Worth Reviewing:      quality_tier == worth_reviewing, diversified —
                           shown with its caveats, never hidden.
  - All Available:        every opportunity that passes the HARD integrity
                           gates (i.e. NOT do_not_headline) — strong +
                           worth_reviewing + speculative — ranked by score,
                           undiversified (Section: "every valid current
                           market... visually distinguish negative/neutral").

do_not_headline itself stays a hard exclusion (stale price, insufficient
model history, confirmed-out player, no eligible price, failed price-
integrity check) — Section 1's safety/data-integrity exclusions are
untouched by this module.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.market_correlation import compute_market_correlations, market_correlation_labels
from app.player_modelling.opportunity_families import diversify, group_into_families
from app.player_modelling.opportunity_reason_codes import compute_reason_codes, reason_labels
from app.player_modelling.opportunity_sanity import filter_sane
from app.player_modelling.prop_odds_freshness import DEFAULT_THRESHOLDS, FreshnessThresholds
from app.player_modelling.quality_tiers import TIER_DO_NOT_HEADLINE, TIER_SPECULATIVE, TIER_STRONG_CANDIDATE, TIER_WORTH_REVIEWING

DEFAULT_BEST_LIMIT = 10
DEFAULT_WORTH_REVIEWING_LIMIT = 15
DEFAULT_ALL_AVAILABLE_LIMIT = 50

FALLBACK_MESSAGE = "No markets currently meet the strongest shortlist criteria. Here are the best available opportunities to review."


def _alternate_summary(o: dict) -> dict:
    return {
        "threshold": o["threshold"], "line_type": o["line_type"], "label": o["label"],
        "model_probability": o["model_probability"], "best_price": o["best_price"], "best_bookmaker": o["best_bookmaker"],
        "difference_pp": o["difference_pp"], "expected_value": o["expected_value"], "n_bookmakers": o["n_bookmakers"],
    }


def _diversify_tier(items: list[dict], match_labels: dict[int, str], *, limit: int | None) -> list[dict]:
    """Same family-grouping/diversification rules as diversified_opportunities.py,
    applied within one quality tier so a single hot player's alternate lines
    can't dominate this tier either. Deliberately skips the recent-form
    enrichment the Best Opportunities view has — this is a leaner, faster
    section, not a replacement for it."""
    families = group_into_families(items, match_labels)
    selected = diversify(families, limit=limit)
    market_correlations = compute_market_correlations([fam.representative for fam in selected])

    enriched = []
    for fam in selected:
        rep = fam.representative
        entry = dict(rep)
        entry["family_label"] = fam.label
        entry["alternate_lines"] = [_alternate_summary(a) for a in fam.alternates]
        entry["correlation_labels"] = market_correlation_labels(rep, market_correlations)
        entry["price_advantage_pct"] = None
        entry["recent_form"] = None
        codes = compute_reason_codes(rep, form_disagreement=False)
        entry["reason_codes"] = codes
        entry["reason_labels"] = reason_labels(codes)
        entry["representative_score"] = rep["opportunity_score"]
        enriched.append(entry)
    return enriched


def _exclusion_breakdown(hard_excluded: list[dict], candidates: list[dict]) -> dict[str, int]:
    """Mirrors quality_tiers.compute_quality_tier's own hard-gate precedence
    exactly (stale -> insufficient history -> confirmed out -> no eligible
    price -> integrity failure) so every hard-excluded item is counted under
    exactly the one reason that actually gated it. `unconfirmed_lineup` and
    `lower_quality_valid` are informational counts within the NON-excluded
    candidate population — those opportunities are visible (Worth Reviewing
    / All Available), not excluded; reported here purely for transparency."""
    reasons = {"stale_odds": 0, "insufficient_history": 0, "confirmed_out": 0, "no_eligible_price": 0, "integrity_failure": 0}
    for o in hard_excluded:
        if o["odds_freshness"] == "stale":
            reasons["stale_odds"] += 1
        elif o["confidence_tier"] == "insufficient_history":
            reasons["insufficient_history"] += 1
        elif o["opportunity_type"] == "player" and o.get("selection_status") == "confirmed_out":
            reasons["confirmed_out"] += 1
        elif not o.get("eligible_price_available", True):
            reasons["no_eligible_price"] += 1
        else:
            reasons["integrity_failure"] += 1

    reasons["unconfirmed_lineup"] = sum(1 for o in candidates if o["opportunity_type"] == "player" and not o.get("is_confirmed"))
    reasons["lower_quality_valid"] = sum(1 for o in candidates if o["quality_tier"]["tier"] == TIER_SPECULATIVE)
    return reasons


@dataclass(frozen=True)
class OpportunityTiersResult:
    best: list[dict]
    worth_reviewing: list[dict]
    all_available: list[dict]
    exclusion_breakdown: dict[str, int]
    n_candidates: int  # valid current opportunities: passes hard integrity checks (strong + worth_reviewing + speculative)
    n_hard_excluded: int
    fallback_message: str | None  # set exactly when `best` is empty and `worth_reviewing` is being shown in its place


def load_opportunity_tiers(
    db: Session,
    *,
    market_scope: str = "all",
    best_limit: int | None = DEFAULT_BEST_LIMIT,
    worth_reviewing_limit: int | None = DEFAULT_WORTH_REVIEWING_LIMIT,
    all_available_limit: int | None = DEFAULT_ALL_AVAILABLE_LIMIT,
    freshness_thresholds: FreshnessThresholds = DEFAULT_THRESHOLDS,
) -> OpportunityTiersResult:
    # Every optional gate OPEN at the source — this module, not
    # load_best_opportunities' own defaults, decides what's visible where.
    raw = load_best_opportunities(
        db, market_scope=market_scope, include_uncertain=True, include_stale=True, include_insufficient_history=True,
        limit=None, freshness_thresholds=freshness_thresholds,
    )
    passed, _sanity_rejected = filter_sane(raw)

    hard_excluded = [o for o in passed if o["quality_tier"]["tier"] == TIER_DO_NOT_HEADLINE]
    candidates = [o for o in passed if o["quality_tier"]["tier"] != TIER_DO_NOT_HEADLINE]

    match_ids = {o["match_id"] for o in candidates}
    matches = {m.id: m for m in db.scalars(select(Match).where(Match.id.in_(match_ids))).all()} if match_ids else {}
    match_labels = {mid: f"{m.home_team.name} v {m.away_team.name}" for mid, m in matches.items()}

    strong = [o for o in candidates if o["quality_tier"]["tier"] == TIER_STRONG_CANDIDATE]
    worth = [o for o in candidates if o["quality_tier"]["tier"] == TIER_WORTH_REVIEWING]

    best = _diversify_tier(strong, match_labels, limit=best_limit)
    worth_reviewing = _diversify_tier(worth, match_labels, limit=worth_reviewing_limit)

    fallback_message = FALLBACK_MESSAGE if not best and worth_reviewing else None

    all_available = sorted(candidates, key=lambda o: o["opportunity_score"], reverse=True)
    if all_available_limit is not None:
        all_available = all_available[:all_available_limit]

    return OpportunityTiersResult(
        best=best, worth_reviewing=worth_reviewing, all_available=all_available,
        exclusion_breakdown=_exclusion_breakdown(hard_excluded, candidates),
        n_candidates=len(candidates), n_hard_excluded=len(hard_excluded), fallback_message=fallback_message,
    )
