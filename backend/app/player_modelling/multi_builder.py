"""Per-match Multi Builder (product feature stage): generates model-
informed multi-leg combinations from the SAME already-computed
opportunities every other view uses (best_opportunities.load_best_
opportunities) — never a second model, never a new probability. Every
leg's model_probability/model_fair_odds/confidence_tier/odds_freshness is
copied unchanged from that existing computation.

Two things this module is explicit about NOT claiming:
  - "Indicative combined odds" is a plain product of each leg's OWN price
    at ONE bookmaker — never presented as a real bookmaker Same Game Multi
    quote (see INDICATIVE_ODDS_LABEL/EXPLANATION). If a provider ever
    supplies a real SGM price, only _indicative_combined_price below needs
    replacing — nothing about tiering/leg-selection/correlation changes.
  - A multi's "combined probability" is never P(A)*P(B)*P(C) for
    correlated legs. Correlation is checked pairwise via
    market_correlation.py (already-existing, rule-based, three-tier
    classification): a STRONG pair (e.g. a team's H2H + that team's line)
    is never combined; a MODERATE pair (e.g. team win + a scorer from that
    team) is allowed but carries an explicit warning. Same-player alternate
    lines are already collapsed to one representative leg per family
    (opportunity_families.py) before a combo is ever built, so they can
    never appear twice in one multi.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.bookmaker import ELIGIBILITY_INCLUDED
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.market_correlation import CORRELATION_STRENGTH, _pair_correlation
from app.player_modelling.opportunity_families import group_into_families, representative_score
from app.player_modelling.quality_tiers import TIER_DO_NOT_HEADLINE

TIER_CONSERVATIVE = "conservative"
TIER_BALANCED = "balanced"
TIER_HIGHER_RETURN = "higher_return"
TIER_LONGER_SHOT = "longer_shot"
TIER_ORDER = [TIER_CONSERVATIVE, TIER_BALANCED, TIER_HIGHER_RETURN, TIER_LONGER_SHOT]
TIER_LABELS = {
    TIER_CONSERVATIVE: "Conservative", TIER_BALANCED: "Balanced",
    TIER_HIGHER_RETURN: "Higher Return", TIER_LONGER_SHOT: "Longer Shot",
}
# (min, max) target combined decimal odds - max=None means no ceiling.
TIER_RANGES: dict[str, tuple[float, float | None]] = {
    TIER_CONSERVATIVE: (1.80, 2.50),
    TIER_BALANCED: (3.00, 5.00),
    TIER_HIGHER_RETURN: (5.00, 10.00),
    TIER_LONGER_SHOT: (10.00, None),
}

MAX_OPTIONS_PER_TIER = 3
MAX_LEGS_PER_MULTI = 5
MAX_APPEARANCES_PER_PLAYER = 2  # across the WHOLE match's multi set (every tier, every option) - no single player dominates

INDICATIVE_ODDS_LABEL = "Indicative combined odds"
INDICATIVE_ODDS_EXPLANATION = (
    "The product of each leg's own decimal price at this bookmaker — not a real Same Game Multi quote. "
    "Actual bookmaker SGM pricing may differ due to correlation between legs and the bookmaker's own multi rules."
)

_CORRELATION_TEXT = {
    "same_team_directional_view": "same underlying team view",
    "team_view_and_match_total": "shares game environment (team view + match total)",
    "same_team_players": "same team as another leg",
    "team_view_and_player": "same team as another leg",
    "match_total_and_player": "shares game environment (match total + a player leg)",
}


def _leg_id(leg: dict) -> tuple:
    return (leg["opportunity_type"], leg.get("player_id"), leg["market_type"], leg.get("selection"), leg.get("line_value"), leg.get("threshold"))


def _reasons_for(leg: dict) -> list[str]:
    reasons = []
    if leg["quality_tier"]["tier"] == "strong_candidate":
        reasons.append("Strong candidate on its own merits")
    if leg.get("is_confirmed"):
        reasons.append("Lineup confirmed")
    if leg["odds_freshness"] == "fresh":
        reasons.append("Fresh odds")
    if leg["confidence_tier"] in ("higher_confidence", "moderate_confidence"):
        reasons.append("Good model confidence")
    if leg.get("n_bookmakers", 0) > 1:
        reasons.append("Multiple bookmakers quote this market")
    if leg["difference_pp"] > 0:
        reasons.append(f"Model favours this by {leg['difference_pp'] * 100:.1f}pp")
    return reasons or ["Passes hard integrity checks"]


def _match_legs(db: Session, match_id: int) -> list[dict]:
    """One representative leg per family (opportunity_families.py) — the
    SAME alternate-line collapsing Best Opportunities already uses, so a
    player's 15+/20+/25+ disposal lines can never all appear in one multi
    as if they were independent legs."""
    raw = load_best_opportunities(
        db, market_scope="all", include_uncertain=True, include_stale=True, include_insufficient_history=True, limit=None,
    )
    match_legs = [o for o in raw if o["match_id"] == match_id and o["quality_tier"]["tier"] != TIER_DO_NOT_HEADLINE]
    if not match_legs:
        return []
    families = group_into_families(match_legs, {match_id: ""})
    return [fam.representative for fam in families]


def _legs_by_bookmaker(legs: list[dict]) -> dict[str, list[dict]]:
    """Only ELIGIBLE (enabled sportsbook) bookmakers - a multi is only ever
    proposed against a bookmaker that actually offers every one of its
    legs, each leg priced at THAT bookmaker (never a cross-bookmaker
    mashup)."""
    by_bookmaker: dict[str, list[dict]] = {}
    for leg in legs:
        for b in leg.get("bookmakers", []):
            if b.get("eligibility") != ELIGIBILITY_INCLUDED:
                continue
            entry = dict(leg)
            entry["bookmaker_price"] = b["price_decimal"]
            by_bookmaker.setdefault(b["bookmaker_name"], []).append(entry)
    return by_bookmaker


def _build_one_combo(
    legs_sorted: list[dict], lo: float, hi: float | None, *, confirmed_only: bool, exclude_ids: set[tuple], player_counts: dict[int, int],
) -> dict | None:
    chosen: list[dict] = []
    chosen_player_ids: set[int] = set()
    combined = 1.0
    warnings: list[str] = []

    for leg in legs_sorted:
        lid = _leg_id(leg)
        if lid in exclude_ids:
            continue
        if confirmed_only and leg["opportunity_type"] == "player" and not leg.get("is_confirmed"):
            continue
        pid = leg.get("player_id")
        if pid is not None:
            if pid in chosen_player_ids:
                continue
            if player_counts.get(pid, 0) >= MAX_APPEARANCES_PER_PLAYER:
                continue

        strong_conflict = False
        pair_warnings = []
        for c in chosen:
            category = _pair_correlation(leg, c)
            if category is None:
                continue
            if CORRELATION_STRENGTH[category] == "strong":
                strong_conflict = True
                break
            pair_warnings.append(f"\"{leg['label']}\" {_CORRELATION_TEXT[category]} as \"{c['label']}\" — not independent, treat with caution")
        if strong_conflict:
            continue

        prospective = combined * leg["bookmaker_price"]
        if hi is not None and prospective > hi and len(chosen) >= 1:
            continue  # would blow past this tier's ceiling - try the next candidate leg instead

        chosen.append(leg)
        if pid is not None:
            chosen_player_ids.add(pid)
        combined = prospective
        warnings.extend(pair_warnings)

        if len(chosen) >= 2 and lo <= combined and (hi is None or combined <= hi):
            break
        if len(chosen) >= MAX_LEGS_PER_MULTI:
            break

    if len(chosen) < 2 or combined < lo or (hi is not None and combined > hi):
        return None

    for pid in chosen_player_ids:
        player_counts[pid] = player_counts.get(pid, 0) + 1

    return {
        "legs": chosen,
        "indicative_combined_odds": combined,
        "n_legs": len(chosen),
        "correlation_warnings": warnings,
        "provisional": any(leg["opportunity_type"] == "player" and not leg.get("is_confirmed") for leg in chosen),
        "lineup_ready": all(leg["opportunity_type"] != "player" or leg.get("is_confirmed") for leg in chosen),
    }


def _options_for_tier(
    legs_by_bookmaker: dict[str, list[dict]], tier_key: str, *, confirmed_only: bool, player_counts: dict[int, int],
) -> list[dict]:
    lo, hi = TIER_RANGES[tier_key]
    # Bookmakers with more usable legs give the greedy builder more room -
    # try them first.
    ranked_bookmakers = sorted(legs_by_bookmaker.items(), key=lambda kv: len(kv[1]), reverse=True)

    options: list[dict] = []
    exclude_ids: set[tuple] = set()
    for _ in range(MAX_OPTIONS_PER_TIER):
        found = None
        for bookmaker_name, legs in ranked_bookmakers:
            legs_sorted = sorted(legs, key=representative_score, reverse=True)
            combo = _build_one_combo(legs_sorted, lo, hi, confirmed_only=confirmed_only, exclude_ids=exclude_ids, player_counts=player_counts)
            if combo is not None:
                combo["bookmaker"] = bookmaker_name
                found = combo
                break
        if found is None:
            break
        options.append(found)
        exclude_ids |= {_leg_id(leg) for leg in found["legs"]}

    for i, opt in enumerate(options):
        opt["option_label"] = f"Option {chr(65 + i)}"
    return options


@dataclass(frozen=True)
class MatchMultiTiers:
    match_id: int
    n_eligible_legs: int
    bookmakers_available: list[str]
    tiers: dict[str, list[dict]] = field(default_factory=dict)


def build_match_multis(db: Session, match_id: int, *, confirmed_only: bool = True) -> MatchMultiTiers:
    legs = _match_legs(db, match_id)
    by_bookmaker = _legs_by_bookmaker(legs)
    player_counts: dict[int, int] = {}

    tiers: dict[str, list[dict]] = {}
    for tier_key in TIER_ORDER:
        tiers[tier_key] = _options_for_tier(by_bookmaker, tier_key, confirmed_only=confirmed_only, player_counts=player_counts)

    return MatchMultiTiers(
        match_id=match_id, n_eligible_legs=len(legs),
        bookmakers_available=sorted(name for name, l in by_bookmaker.items() if len(l) >= 2),
        tiers=tiers,
    )


def option_as_dict(opt: dict) -> dict:
    return {
        "option_label": opt["option_label"],
        "bookmaker": opt["bookmaker"],
        "n_legs": opt["n_legs"],
        "indicative_combined_odds": opt["indicative_combined_odds"],
        "indicative_odds_label": INDICATIVE_ODDS_LABEL,
        "indicative_odds_explanation": INDICATIVE_ODDS_EXPLANATION,
        "provisional": opt["provisional"],
        "lineup_ready": opt["lineup_ready"],
        "correlation_warnings": opt["correlation_warnings"],
        "average_confidence_component": sum(leg["opportunity_components"]["confidence"] for leg in opt["legs"]) / len(opt["legs"]),
        "legs": [
            {
                "opportunity_type": leg["opportunity_type"], "label": leg["label"], "market_type": leg["market_type"],
                "player_id": leg.get("player_id"), "player_name": leg.get("player_name"), "team_id": leg.get("team_id"),
                "bookmaker_price": leg["bookmaker_price"], "model_probability": leg["model_probability"],
                "model_fair_odds": leg["model_fair_odds"], "difference_pp": leg["difference_pp"],
                "confidence_tier": leg["confidence_tier"], "selection_status": leg.get("selection_status"),
                "is_confirmed": leg.get("is_confirmed"), "odds_freshness": leg["odds_freshness"],
                "warnings": leg.get("warnings", []), "reasons": _reasons_for(leg),
            }
            for leg in opt["legs"]
        ],
    }


def match_multi_tiers_as_dict(result: MatchMultiTiers) -> dict:
    return {
        "match_id": result.match_id,
        "n_eligible_legs": result.n_eligible_legs,
        "bookmakers_available": result.bookmakers_available,
        "tiers": [
            {"tier": tier_key, "label": TIER_LABELS[tier_key], "options": [option_as_dict(o) for o in result.tiers.get(tier_key, [])]}
            for tier_key in TIER_ORDER
        ],
    }
