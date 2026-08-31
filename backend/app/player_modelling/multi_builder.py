"""Per-match Multi Builder (product feature stage): generates model-
informed multi-leg combinations from the SAME already-computed
opportunities every other view uses (best_opportunities.load_best_
opportunities) — never a second model, never a new probability. Every
leg's model_probability/model_fair_odds/confidence_tier/odds_freshness is
copied unchanged from that existing computation.

Two modes, selectable independently of tier (see MODE_HIGH_PROBABILITY /
MODE_VALUE), sourced from two DIFFERENT candidate pools built from that
same underlying data:
  - High Probability (the default): ranks and searches candidate legs
    primarily by their OWN model probability (then confidence/threshold-
    specific calibration/confirmed lineup/freshness/recent-form warnings,
    edge only last, minor). Built from _all_alternate_legs — EVERY valid
    fresh alternate threshold line, never collapsed down to one
    value-ranked "representative" per player/market first, so a player's
    15+/20+/25+ disposal lines are all independently available and a
    genuinely likely line is never hidden behind a punchier-but-riskier
    one. A leg like "82% model probability, $1.25, modest edge" is
    exactly what this mode is FOR, not something to filter out — nor is
    "92% model probability, $1.10, tiny edge" something a large-edge-but-
    52%-probability leg should be able to outrank here.
  - Value: ranks by the existing transparent opportunity_score (edge/EV-
    led), built from the existing collapsed _match_legs pool — unchanged
    from before this stage.
Both modes still refuse a leg the model considers CLEARLY overpriced (see
MIN_EDGE_FLOOR_HIGH_PROBABILITY — a small, configurable, near-neutral
tolerance for High Probability; Value mode leans on its own score
ranking instead of a second hard floor).

Combination search, not greedy top-2: for each tier this searches actual
2..N-leg COMBINATIONS (N per MAX_LEGS below) of the mode-ranked candidate
pool and picks the one that best satisfies the tier's odds band and the
mode's own quality criteria (e.g. High Probability maximises the WEAKEST
leg's probability first, not combined odds or raw EV).

Three things this module is explicit about NOT claiming:
  - "Indicative combined odds" is a plain product of each leg's OWN price
    at ONE bookmaker — never presented as a real bookmaker Same Game Multi
    quote (see INDICATIVE_ODDS_LABEL/EXPLANATION). If a provider ever
    supplies a real SGM price, only that product needs replacing —
    nothing about tiering/leg-selection/correlation changes.
  - A multi's "combined probability" is never P(A)*P(B)*P(C) for
    correlated legs, and is never shown at all — only each leg's OWN
    probability, plus the lowest/average across the multi (see
    option_as_dict). Presenting a joint probability would require a
    validated correlation model this app doesn't have.
  - Correlation is checked pairwise via market_correlation.py (already-
    existing, rule-based, three-tier classification): a STRONG pair (e.g.
    a team's H2H + that team's line) is never combined; a MODERATE pair
    (e.g. team win + a scorer from that team) is allowed but carries an
    explicit warning. Same-player/same-family alternate lines can never
    both appear in one combo (see _family_key exclusivity below).
"""

from dataclasses import dataclass, field
from itertools import combinations
from statistics import mean

from sqlalchemy.orm import Session

from app.models.bookmaker import ELIGIBILITY_INCLUDED
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.market import PlayerMarket
from app.player_modelling.market_correlation import CORRELATION_STRENGTH, _pair_correlation
from app.player_modelling.opportunity_families import family_key, group_into_families, representative_score
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
    TIER_BALANCED: (2.50, 5.00),
    TIER_HIGHER_RETURN: (5.00, 10.00),
    TIER_LONGER_SHOT: (10.00, None),
}

# Ranges, not targets (see module docstring) - fewer legs are preferred
# when they already reach the target band, but a 4-5 leg combination of
# individually strong legs is never artificially capped at 2.
MIN_LEGS: dict[str, int] = {TIER_CONSERVATIVE: 2, TIER_BALANCED: 2, TIER_HIGHER_RETURN: 3, TIER_LONGER_SHOT: 3}
MAX_LEGS: dict[str, int] = {TIER_CONSERVATIVE: 5, TIER_BALANCED: 6, TIER_HIGHER_RETURN: 7, TIER_LONGER_SHOT: 8}

MODE_HIGH_PROBABILITY = "high_probability"
MODE_VALUE = "value"
DEFAULT_MODE = MODE_HIGH_PROBABILITY

# High-Probability mode's per-tier minimum individual-leg model
# probability - configurable, not hardcoded into the search itself. Every
# tier sits above 50% per leg; Conservative/Balanced deliberately demand a
# genuinely high individual chance since that's the entire point of the
# mode (a 2+ goals leg at 51% must never qualify here just because its
# bookmaker price is generous - see the module docstring's goals example).
MIN_LEG_PROBABILITY: dict[str, float] = {
    TIER_CONSERVATIVE: 0.75, TIER_BALANCED: 0.65, TIER_HIGHER_RETURN: 0.55, TIER_LONGER_SHOT: 0.50,
}
# A small, configurable tolerance for near-neutral value: High-Probability
# mode's OWN qualification is the leg's probability, not its edge, so a
# leg sitting right at (or a touch below) neutral is still allowed. This
# is deliberately small - nowhere near enough to admit a leg the model
# considers clearly overpriced just to pad a leg count. Value mode leans
# on the existing opportunity_score ranking for its stronger edge
# preference rather than this floor.
MIN_EDGE_FLOOR_HIGH_PROBABILITY = -0.02

# Value mode's own "sensible minimum individual probability" (item 3) - a
# flat sanity floor, deliberately looser than High-Probability's per-tier
# floors since Value mode's whole point is ranking by edge/EV, not
# probability. Still never lets a near-coinflip-or-worse leg into a
# "value" multi just because its EV is good.
MIN_LEG_PROBABILITY_VALUE = 0.40

# Item 7: goals need stricter handling than disposals. 2+/3+ goals legs
# already can't reach Conservative/Balanced on probability alone in most
# cases (their floors are 75%/65%), but a leg that DOES clear the floor
# while the goal model itself is flagging a recent usage-regime change
# (see goal_usage_risk_flags) is still excluded from the two "safer" tiers
# specifically - the model's own calibration warning is a reason for
# caution the raw probability number doesn't capture. Higher Return/Longer
# Shot tiers may still use these legs; this never touches raw probability.
GOALS_STRICT_TIERS = (TIER_CONSERVATIVE, TIER_BALANCED)
GOALS_STRICT_MIN_THRESHOLD = 2.0

MAX_OPTIONS_PER_TIER = 3
MAX_APPEARANCES_PER_PLAYER = 2  # across the WHOLE match's multi set (every tier, every option) - no single player dominates
_MAX_POOL_SIZE = 16  # candidate legs considered per bookmaker, per tier+mode - keeps combination search fast without changing which legs WIN
_MAX_BOOKMAKERS_SEARCHED = 5  # ranked by how many usable legs they offer

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


def _safest_family_member(fam) -> dict | None:
    """opportunity_families.representative_score deliberately PENALISES an
    extreme-short price (see its _price_extremity_multiplier) because for a
    single standalone bet, a $1.05 favourite is rarely good VALUE even when
    likely to win. High-Probability mode wants the opposite property most:
    a genuinely high probability of winning. This picks, from the SAME
    family the representative came from, whichever member has the highest
    model probability while still keeping a real (non-negative) model-
    market edge — never a leg the model doesn't actually favour."""
    candidates = [m for m in (fam.representative, *fam.alternates) if m["difference_pp"] >= MIN_EDGE_FLOOR_HIGH_PROBABILITY]
    if not candidates:
        return None
    return max(candidates, key=lambda o: o["model_probability"])


# Structured reason/warning codes (item 14) - every code below is derived
# straight from already-computed leg fields, never a new judgement. Kept as
# {code, label} pairs so a consumer can branch on `code` (stable) while
# still getting a ready-to-display `label` (may include a computed number).
REASON_HIGH_MODEL_PROBABILITY = "HIGH_MODEL_PROBABILITY"
REASON_CONFIRMED_SELECTED = "CONFIRMED_SELECTED"
REASON_WELL_CALIBRATED_THRESHOLD = "WELL_CALIBRATED_THRESHOLD"
REASON_FRESH_MARKET = "FRESH_MARKET"
REASON_POSITIVE_MODEL_EDGE = "POSITIVE_MODEL_EDGE"
REASON_GOOD_CONFIDENCE = "GOOD_CONFIDENCE"
REASON_MULTIPLE_BOOKMAKERS = "MULTIPLE_BOOKMAKERS"
REASON_PASSES_INTEGRITY_CHECKS = "PASSES_INTEGRITY_CHECKS"

WARNING_TEAMS_NOT_CONFIRMED = "TEAMS_NOT_CONFIRMED"
WARNING_RECENT_USAGE_REGIME_CHANGE = "RECENT_USAGE_REGIME_CHANGE"
WARNING_LOWER_CONFIDENCE = "LOWER_CONFIDENCE"
WARNING_SINGLE_BOOK_MARKET = "SINGLE_BOOK_MARKET"


def _reasons_for(leg: dict) -> list[dict]:
    reasons = []
    if leg["quality_tier"]["tier"] == "strong_candidate":
        reasons.append({"code": REASON_WELL_CALIBRATED_THRESHOLD, "label": "Strong candidate on its own merits"})
    if leg.get("is_confirmed"):
        reasons.append({"code": REASON_CONFIRMED_SELECTED, "label": "Lineup confirmed"})
    if leg["odds_freshness"] == "fresh":
        reasons.append({"code": REASON_FRESH_MARKET, "label": "Fresh odds"})
    if leg["confidence_tier"] in ("higher_confidence", "moderate_confidence"):
        reasons.append({"code": REASON_GOOD_CONFIDENCE, "label": "Good model confidence"})
    if leg.get("n_bookmakers", 0) > 1:
        reasons.append({"code": REASON_MULTIPLE_BOOKMAKERS, "label": "Multiple bookmakers quote this market"})
    if leg["model_probability"] >= 0.70:
        reasons.append({"code": REASON_HIGH_MODEL_PROBABILITY, "label": f"High model probability ({leg['model_probability'] * 100:.0f}%)"})
    if leg["difference_pp"] > 0:
        reasons.append({"code": REASON_POSITIVE_MODEL_EDGE, "label": f"Model favours this by {leg['difference_pp'] * 100:.1f}pp"})
    return reasons or [{"code": REASON_PASSES_INTEGRITY_CHECKS, "label": "Passes hard integrity checks"}]


def _warnings_for(leg: dict) -> list[dict]:
    warnings = []
    if leg["opportunity_type"] == "player" and not leg.get("is_confirmed"):
        warnings.append({"code": WARNING_TEAMS_NOT_CONFIRMED, "label": "Teams not confirmed for this match yet."})
    if leg.get("model_risk_flags"):
        warnings.append({"code": WARNING_RECENT_USAGE_REGIME_CHANGE, "label": "; ".join(f["description"] for f in leg["model_risk_flags"])})
    if leg["confidence_tier"] == "lower_confidence":
        warnings.append({"code": WARNING_LOWER_CONFIDENCE, "label": "Lower model confidence for this leg."})
    if leg.get("n_bookmakers", 0) <= 1:
        warnings.append({"code": WARNING_SINGLE_BOOK_MARKET, "label": "Only one bookmaker quotes this market."})
    return warnings


def _match_legs(db: Session, match_id: int, *, raw_opportunities: list[dict] | None = None) -> list[dict]:
    """One representative leg per family (opportunity_families.py) PLUS,
    where it differs, the family's safest high-probability alternate (see
    _safest_family_member) — the SAME alternate-line collapsing Best
    Opportunities uses, extended with a second candidate so High-
    Probability mode has a genuinely safe line to reach for, never two
    lines from the same player/team market appearing as if independent."""
    raw = raw_opportunities if raw_opportunities is not None else load_best_opportunities(
        db, market_scope="all", include_uncertain=True, include_stale=True, include_insufficient_history=True, limit=None,
    )
    match_legs = [o for o in raw if o["match_id"] == match_id and o["quality_tier"]["tier"] != TIER_DO_NOT_HEADLINE]
    if not match_legs:
        return []
    families = group_into_families(match_legs, {match_id: ""})

    result: list[dict] = []
    for fam in families:
        rep = dict(fam.representative)
        rep["_family_key"] = fam.key
        result.append(rep)

        safest = _safest_family_member(fam)
        if safest is not None and _leg_id(safest) != _leg_id(fam.representative):
            alt = dict(safest)
            alt["_family_key"] = fam.key
            result.append(alt)
    return result


def _all_alternate_legs(db: Session, match_id: int, *, raw_opportunities: list[dict] | None = None) -> list[dict]:
    """High-Probability mode's candidate source — deliberately NOT
    _match_legs/group_into_families: that pipeline collapses every
    player+market family down to one value-ranked representative plus at
    most one "safest alternate," which silently discards other threshold
    lines (e.g. a player's 15+/20+/25+ disposal lines) before High-
    Probability mode ever sees them. This instead keeps EVERY individual
    alternate line that passes the same hard integrity gate (fresh,
    non-stale, sufficient history, no confirmed-out/price-integrity
    failure) as its own independent candidate, still tagged with the same
    family_key so _combo_valid can enforce "only one line per player's
    disposal/goal market in a single multi" — that's a per-combination
    validity rule, not a reason to hide candidates from the ranking/search
    itself."""
    raw = raw_opportunities if raw_opportunities is not None else load_best_opportunities(
        db, market_scope="all", include_uncertain=True, include_stale=True, include_insufficient_history=True, limit=None,
    )
    result = []
    for o in raw:
        if o["match_id"] != match_id or o["quality_tier"]["tier"] == TIER_DO_NOT_HEADLINE:
            continue
        leg = dict(o)
        leg["_family_key"] = family_key(o)
        result.append(leg)
    return result


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


def _high_probability_score(leg: dict) -> tuple:
    """Lexicographic priority exactly as specified: model probability,
    then confidence, then threshold-specific calibration, then confirmed
    lineup, then freshness/integrity, then recent-form/context warnings,
    then (last, minor) model-market edge. Confidence/calibration/
    freshness/lineup are the SAME named, already-visible components
    opportunity_score itself uses (prop_opportunity_ranking.py) — reused,
    not reinvented."""
    comp = leg["opportunity_components"]
    lineup_ok = leg["opportunity_type"] != "player" or bool(leg.get("is_confirmed"))
    n_warnings = len(leg.get("warnings", []))
    return (
        leg["model_probability"], comp["confidence"], comp["calibration"],
        1.0 if lineup_ok else 0.0, comp["freshness"], -n_warnings, leg["difference_pp"],
    )


def _is_strict_goals_excluded(leg: dict, tier_key: str) -> bool:
    """Item 7: a goals leg at 2+ or higher, carrying an active model-risk
    flag (recent usage-regime change), is excluded from the two "safer"
    tiers even if its raw probability clears the floor - the model's own
    calibration warning is exactly the kind of caution those tiers exist
    to respect. Disposals and 1+ goals are never affected."""
    if tier_key not in GOALS_STRICT_TIERS:
        return False
    if leg["market_type"] != PlayerMarket.GOALS.value:
        return False
    threshold = leg.get("threshold")
    if threshold is None or threshold < GOALS_STRICT_MIN_THRESHOLD:
        return False
    return bool(leg.get("model_risk_flags"))


def _candidate_pool(legs: list[dict], tier_key: str, mode: str, *, confirmed_only: bool) -> list[dict]:
    pool = legs
    if confirmed_only:
        pool = [leg for leg in pool if leg["opportunity_type"] != "player" or leg.get("is_confirmed")]
    if mode == MODE_HIGH_PROBABILITY:
        min_prob = MIN_LEG_PROBABILITY[tier_key]
        pool = [
            leg for leg in pool
            if leg["model_probability"] >= min_prob and leg["difference_pp"] >= MIN_EDGE_FLOOR_HIGH_PROBABILITY
            and not _is_strict_goals_excluded(leg, tier_key)
        ]
        pool = sorted(pool, key=_high_probability_score, reverse=True)
    else:
        pool = [leg for leg in pool if leg["model_probability"] >= MIN_LEG_PROBABILITY_VALUE]
        pool = sorted(pool, key=representative_score, reverse=True)
    return pool[:_MAX_POOL_SIZE]


def _combo_valid(
    combo: tuple[dict, ...], lo: float, hi: float | None, exclude_ids: set[tuple], exclude_family_keys: set[tuple],
    player_counts: dict[int, int],
) -> tuple[float, list[str]] | None:
    """Returns (combined_odds, correlation_warnings) if this combination is
    legal and lands in the tier's odds band, else None."""
    family_keys = [leg["_family_key"] for leg in combo]
    if len(set(family_keys)) != len(family_keys) or any(fk in exclude_family_keys for fk in family_keys):
        return None
    player_ids = [leg["player_id"] for leg in combo if leg.get("player_id") is not None]
    if len(set(player_ids)) != len(player_ids):
        return None
    if any(player_counts.get(pid, 0) >= MAX_APPEARANCES_PER_PLAYER for pid in player_ids):
        return None  # whole-match diversity cap - no single player fills every tier
    if any(_leg_id(leg) in exclude_ids for leg in combo):
        return None

    combined = 1.0
    warnings: list[str] = []
    for leg in combo:
        combined *= leg["bookmaker_price"]
    if combined < lo or (hi is not None and combined > hi):
        return None

    for a, b in combinations(combo, 2):
        category = _pair_correlation(a, b)
        if category is None:
            continue
        if CORRELATION_STRENGTH[category] == "strong":
            return None
        warnings.append(f"\"{a['label']}\" {_CORRELATION_TEXT[category]} as \"{b['label']}\" — not independent, treat with caution")

    return combined, warnings


def _combo_rank_key(combo: tuple[dict, ...], warnings: list[str], mode: str) -> tuple:
    probs = [leg["model_probability"] for leg in combo]
    n_confirmed = sum(1 for leg in combo if leg["opportunity_type"] != "player" or leg.get("is_confirmed"))
    if mode == MODE_HIGH_PROBABILITY:
        # Exactly the order specified: (1) target odds band is a hard
        # filter in _combo_valid, not part of this key; (2) maximise the
        # WEAKEST leg's probability (a multi is only as safe as its
        # shakiest leg); (3) maximise average probability; (4) maximise
        # average confidence/calibration quality; (5) minimise
        # correlation (fewer/no moderate-correlation warnings); (6)
        # prefer confirmed players; (7) only then consider additional
        # model-market value; fewer legs is the final tiebreak.
        avg_quality = mean((leg["opportunity_components"]["confidence"] + leg["opportunity_components"]["calibration"]) / 2 for leg in combo)
        avg_edge = mean(leg["difference_pp"] for leg in combo)
        # Usage-Change Production Integration stage, item 4: a genuinely
        # LAST-priority caution tiebreak — never touches model_probability
        # or any earlier ranking criterion, and only decides between combos
        # that are otherwise tied on everything above. Prefers fewer
        # usage-regime-changed goal legs when two combos are equal in
        # every other respect (research found no held-out support for a
        # harder exclusion — see usage_regime_change_research.py).
        n_risk_flagged = sum(1 for leg in combo if leg.get("model_risk_flags"))
        return (min(probs), mean(probs), avg_quality, -len(warnings), n_confirmed, avg_edge, -len(combo), -n_risk_flagged)
    # Value mode: prioritise average model-market edge/value (the existing
    # transparent opportunity score), never raw combined odds.
    avg_value_score = mean(representative_score(leg) for leg in combo)
    return (avg_value_score, n_confirmed, -len(combo))


def _search_tier_bookmaker(
    pool: list[dict], lo: float, hi: float | None, min_legs: int, max_legs: int, mode: str,
    exclude_ids: set[tuple], exclude_family_keys: set[tuple], player_counts: dict[int, int],
) -> tuple | None:
    """Evaluates real 2..max_legs COMBINATIONS from `pool` (already ranked
    and capped) rather than greedily taking the top 2 — a 4-5 leg
    combination of individually strong legs can legitimately beat a
    2-legger for this tier's own quality criteria."""
    best = None
    best_key = None
    for size in range(min_legs, min(max_legs, len(pool)) + 1):
        for combo in combinations(pool, size):
            check = _combo_valid(combo, lo, hi, exclude_ids, exclude_family_keys, player_counts)
            if check is None:
                continue
            combined_odds, warnings = check
            key = _combo_rank_key(combo, warnings, mode)
            if best is None or key > best_key:
                best, best_key = (combo, combined_odds, warnings), key
    return best


@dataclass(frozen=True)
class TierResult:
    options: list[dict]
    unavailable_reason: str | None
    bookmaker_comparison: list[dict]  # item 9: every bookmaker's own best achievable combo for this tier, not just the winner


def _options_for_tier(
    legs_by_bookmaker: dict[str, list[dict]], tier_key: str, mode: str, *, confirmed_only: bool, player_counts: dict[int, int],
) -> TierResult:
    lo, hi = TIER_RANGES[tier_key]
    min_legs, max_legs = MIN_LEGS[tier_key], MAX_LEGS[tier_key]

    pools_by_bookmaker = {
        name: _candidate_pool(legs, tier_key, mode, confirmed_only=confirmed_only) for name, legs in legs_by_bookmaker.items()
    }
    ranked_bookmakers = sorted(
        ((name, pool) for name, pool in pools_by_bookmaker.items() if len(pool) >= min_legs),
        key=lambda kv: len(kv[1]), reverse=True,
    )[:_MAX_BOOKMAKERS_SEARCHED]

    if not ranked_bookmakers:
        min_prob = MIN_LEG_PROBABILITY.get(tier_key) if mode == MODE_HIGH_PROBABILITY else MIN_LEG_PROBABILITY_VALUE
        reason = (
            f"No {TIER_LABELS[tier_key]} multi currently meets the required individual-leg probabilities "
            f"(needs at least {min_legs} legs each {min_prob * 100:.0f}%+, from the same bookmaker)."
            if mode == MODE_HIGH_PROBABILITY else
            f"No {TIER_LABELS[tier_key]} multi currently has enough sensible-probability value legs from a single bookmaker."
        )
        return TierResult(options=[], unavailable_reason=reason, bookmaker_comparison=[])

    options: list[dict] = []
    bookmaker_comparison: list[dict] = []
    exclude_ids: set[tuple] = set()
    exclude_family_keys: set[tuple] = set()
    for pass_index in range(MAX_OPTIONS_PER_TIER):
        best_overall = None
        best_overall_key = None
        best_bookmaker = None
        for bookmaker_name, pool in ranked_bookmakers:
            found = _search_tier_bookmaker(pool, lo, hi, min_legs, max_legs, mode, exclude_ids, exclude_family_keys, player_counts)
            if pass_index == 0:
                # Item 9: capture every bookmaker's OWN best combo on this
                # first pass (before any leg exclusions from chosen options
                # apply), so "which bookmaker offers the best indicative
                # combination" can be answered even for bookmakers that
                # don't end up winning.
                if found is not None:
                    bookmaker_comparison.append({"bookmaker": bookmaker_name, "indicative_combined_odds": found[1], "n_legs": len(found[0])})
            if found is None:
                continue
            combo, combined_odds, warnings = found
            key = _combo_rank_key(combo, warnings, mode)
            if best_overall is None or key > best_overall_key:
                best_overall, best_overall_key, best_bookmaker = (combo, combined_odds, warnings), key, bookmaker_name
        if best_overall is None:
            break

        combo, combined_odds, warnings = best_overall
        chosen_player_ids = {leg["player_id"] for leg in combo if leg.get("player_id") is not None}
        # Diversity cap: skip legs that would push a player over their
        # whole-match appearance budget, by excluding this combo's own
        # over-budget players from CONSIDERATION on the next pass, rather
        # than rejecting an otherwise-best combo entirely.
        for pid in chosen_player_ids:
            player_counts[pid] = player_counts.get(pid, 0) + 1

        option = {
            "legs": list(combo),
            "indicative_combined_odds": combined_odds,
            "n_legs": len(combo),
            "correlation_warnings": warnings,
            "provisional": any(leg["opportunity_type"] == "player" and not leg.get("is_confirmed") for leg in combo),
            "lineup_ready": all(leg["opportunity_type"] != "player" or leg.get("is_confirmed") for leg in combo),
            "bookmaker": best_bookmaker,
            "mode": mode,
            "lowest_leg_probability": min(leg["model_probability"] for leg in combo),
            "average_leg_probability": mean(leg["model_probability"] for leg in combo),
        }
        options.append(option)
        exclude_ids |= {_leg_id(leg) for leg in combo}
        exclude_family_keys |= {leg["_family_key"] for leg in combo}

    for i, opt in enumerate(options):
        opt["option_label"] = f"Option {chr(65 + i)}"

    unavailable_reason = None
    if not options:
        unavailable_reason = (
            f"Enough {TIER_LABELS[tier_key].lower()}-eligible legs exist, but none combine to reach the "
            f"${lo:.2f}{f'-${hi:.2f}' if hi else '+'} odds band from a single bookmaker without breaking a correlation rule."
        )
    bookmaker_comparison.sort(key=lambda c: c["indicative_combined_odds"])
    return TierResult(options=options, unavailable_reason=unavailable_reason, bookmaker_comparison=bookmaker_comparison)


@dataclass(frozen=True)
class MatchMultiTiers:
    match_id: int
    n_eligible_legs: int
    bookmakers_available: list[str]
    tiers: dict[str, TierResult] = field(default_factory=dict)


def build_match_multis(
    db: Session, match_id: int, *, confirmed_only: bool = True, mode: str = DEFAULT_MODE, raw_opportunities: list[dict] | None = None,
) -> MatchMultiTiers:
    # High-Probability mode sources from EVERY alternate threshold line
    # (see _all_alternate_legs); Value mode keeps the existing collapsed
    # one-representative-plus-safest-alternate pool (_match_legs), unchanged.
    # `raw_opportunities`, when supplied, is used as-is instead of each
    # helper independently re-scanning load_best_opportunities() — same
    # passthrough pattern compute_match_readiness already uses, purely to
    # avoid redundant identical work within a single request.
    legs = (
        _all_alternate_legs(db, match_id, raw_opportunities=raw_opportunities) if mode == MODE_HIGH_PROBABILITY
        else _match_legs(db, match_id, raw_opportunities=raw_opportunities)
    )
    by_bookmaker = _legs_by_bookmaker(legs)
    player_counts: dict[int, int] = {}

    tiers: dict[str, TierResult] = {}
    for tier_key in TIER_ORDER:
        tiers[tier_key] = _options_for_tier(by_bookmaker, tier_key, mode, confirmed_only=confirmed_only, player_counts=player_counts)

    return MatchMultiTiers(
        match_id=match_id, n_eligible_legs=len(legs),
        bookmakers_available=sorted(name for name, l in by_bookmaker.items() if len(l) >= 2),
        tiers=tiers,
    )


def option_as_dict(opt: dict) -> dict:
    return {
        "option_label": opt["option_label"],
        "mode": opt["mode"],
        "bookmaker": opt["bookmaker"],
        "n_legs": opt["n_legs"],
        "indicative_combined_odds": opt["indicative_combined_odds"],
        "indicative_odds_label": INDICATIVE_ODDS_LABEL,
        "indicative_odds_explanation": INDICATIVE_ODDS_EXPLANATION,
        "provisional": opt["provisional"],
        "lineup_ready": opt["lineup_ready"],
        "correlation_warnings": opt["correlation_warnings"],
        # Item 14: LOW_CORRELATION is a whole-combination property (no
        # pairwise flag fired), not any one leg's own trait.
        "reason_codes": [] if opt["correlation_warnings"] else ["LOW_CORRELATION"],
        "lowest_leg_probability": opt["lowest_leg_probability"],
        "average_leg_probability": opt["average_leg_probability"],
        "average_confidence_component": sum(leg["opportunity_components"]["confidence"] for leg in opt["legs"]) / len(opt["legs"]),
        "legs": [
            {
                "opportunity_type": leg["opportunity_type"], "label": leg["label"], "market_type": leg["market_type"],
                "player_id": leg.get("player_id"), "player_name": leg.get("player_name"), "team_id": leg.get("team_id"),
                "bookmaker_price": leg["bookmaker_price"], "model_probability": leg["model_probability"],
                "model_fair_odds": leg["model_fair_odds"], "difference_pp": leg["difference_pp"],
                "confidence_tier": leg["confidence_tier"], "selection_status": leg.get("selection_status"),
                "is_confirmed": leg.get("is_confirmed"), "odds_freshness": leg["odds_freshness"],
                "warnings": leg.get("warnings", []), "reasons": _reasons_for(leg), "warning_codes": _warnings_for(leg),
                "usage_regime": leg.get("usage_regime"), "model_risk_flags": leg.get("model_risk_flags", []),
                "model_name": leg.get("model_name"), "model_version": leg.get("model_version"),
                "calibration_known": leg.get("calibration") is not None,
                # Exposed so a leg can be frozen into a Placed Bets record
                # (app/player_modelling/placed_bets.py) with the exact
                # selection/line it represents - not used by any ranking
                # or combo-validity logic, which already reads these
                # straight off the underlying leg dict (see _combo_key).
                "selection": leg.get("selection"), "threshold": leg.get("threshold"), "line_type": leg.get("line_type"),
                "line_value": leg.get("line_value"),
            }
            for leg in opt["legs"]
        ],
    }


_SGM_LEG_TYPE_MAP = {PlayerMarket.DISPOSALS.value: "disposals", PlayerMarket.GOALS.value: "goals"}
_SGM_N_SIMULATIONS = 20_000  # smaller than the dedicated pricing API's 100k default - this runs once per rendered option, several times per request


def _try_price_same_game(db: Session, match_id: int, opt: dict) -> dict | None:
    """Best-effort Same Game Multi joint-probability enrichment via
    app/pricing/same_game_pricing.py's conditional Monte Carlo engine.
    ADDITIVE ONLY: never changes which combos are searched/ranked/selected
    above (this runs strictly after a combo is already chosen), never
    replaces indicative_combined_odds, and any reason this can't be priced
    - a "line" team leg (handicap sign convention isn't safely inferable
    from this dict alone), 2+ team legs, no player leg, a missing
    projection row, or a strong-correlation pair the engine itself would
    reject - simply returns None so the option renders exactly as it did
    before this existed."""
    team_legs = [leg for leg in opt["legs"] if leg["opportunity_type"] == "team" and leg["market_type"] in ("h2h", "total")]
    unsupported_team_legs = [leg for leg in opt["legs"] if leg["opportunity_type"] == "team" and leg["market_type"] not in ("h2h", "total")]
    player_legs = [leg for leg in opt["legs"] if leg["opportunity_type"] == "player"]
    if unsupported_team_legs or len(team_legs) > 1 or not player_legs:
        return None

    from app.edges.calculator import ModelsUnavailableError
    from app.pricing.same_game_pricing import SgmLegRequest, SgmValidationError, price_same_game_multi

    requests = []
    for leg in team_legs:
        requests.append(SgmLegRequest(
            leg_type=leg["market_type"], team_id=leg.get("team_id"),
            is_over=(leg.get("selection", "") or "").lower() != "under", line_value=leg.get("line_value"),
        ))
    for leg in player_legs:
        mapped_type = _SGM_LEG_TYPE_MAP.get(leg["market_type"])
        if mapped_type is None or leg.get("threshold") is None or leg.get("player_id") is None:
            return None
        requests.append(SgmLegRequest(leg_type=mapped_type, player_id=leg["player_id"], threshold=leg["threshold"]))

    try:
        price = price_same_game_multi(db, match_id, requests, n_simulations=_SGM_N_SIMULATIONS)
    except (SgmValidationError, ModelsUnavailableError):
        # ModelsUnavailableError: elo_cli/poisson_cli haven't been run yet in
        # this environment - Multi Builder itself doesn't need those to list
        # legs (probabilities are already baked into best_opportunities), so
        # this enrichment degrading to "unavailable" rather than raising
        # matches its own "additive only" contract.
        return None

    return {
        "model_joint_probability": price.model_probability, "model_joint_fair_odds": price.model_fair_odds,
        "naive_independence_probability": price.naive_independence_probability,
        "correlation_adjustment_pp": price.correlation_adjustment_pp, "dependence_validated": price.dependence_validated,
        "model_version": price.model_version, "n_simulations": price.n_simulations, "mc_standard_error": price.mc_standard_error,
        # Not part of the product-facing MultiOptionRead schema (Phase 5) -
        # carried here purely so app/pricing/sgm_snapshot_service.py can
        # freeze a fully self-contained, auditable price without a second
        # pricing call. See same_game_pricing.SameGameMultiPrice's docstring
        # for why the raw values (not just a version string) matter.
        "_dependence_coefficients_used": price.dependence_coefficients_used,
        "_naive_independence_fair_odds": price.naive_independence_fair_odds,
    }


def _option_with_sgm_pricing(db: Session, match_id: int, opt: dict) -> dict:
    option_dict = option_as_dict(opt)
    option_dict["same_game_pricing"] = _try_price_same_game(db, match_id, option_dict)
    return option_dict


def match_multi_tiers_as_dict(db: Session, result: MatchMultiTiers) -> dict:
    return {
        "match_id": result.match_id,
        "n_eligible_legs": result.n_eligible_legs,
        "bookmakers_available": result.bookmakers_available,
        "tiers": [
            {
                "tier": tier_key, "label": TIER_LABELS[tier_key],
                "options": [_option_with_sgm_pricing(db, result.match_id, o) for o in result.tiers[tier_key].options] if tier_key in result.tiers else [],
                "unavailable_reason": result.tiers[tier_key].unavailable_reason if tier_key in result.tiers else None,
                "bookmaker_comparison": result.tiers[tier_key].bookmaker_comparison if tier_key in result.tiers else [],
            }
            for tier_key in TIER_ORDER
        ],
    }
