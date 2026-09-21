"""Per-match Multi Builder (product feature stage): generates model-
informed multi-leg combinations from the SAME already-computed
opportunities every other view uses (best_opportunities.load_best_
opportunities) — never a second model, never a new probability. Every
leg's model_probability/model_fair_odds/confidence_tier/odds_freshness is
copied unchanged from that existing computation.

Selection refinement (multi-builder-selection-refinement): the original
High Probability objective ranked legs, and then whole combinations, by the
weakest leg's raw model probability first and leg count almost last. The
audit in scripts/multi_builder_audit.py found the model's leg probabilities
are reasonably calibrated, so the poor product was not "bad probabilities" -
it was that maximising the weakest leg's probability drives the search to
the SHORTEST-PRICED legs (easy low thresholds, often from low-volume
players), and short prices force MANY legs to reach a tier's odds band,
which multiplies both bookmaker margin and the number of ways to miss. The
selection now, in order of preference:
  1. considers only "main market" legs (see market_relevance) unless the
     user allows less common lines, and only relaxes a preference (main
     markets / confirmed lineups) when the stricter pool cannot fill a tier;
  2. keeps, per player-market, only the most demanding lines that still
     clear the tier's own probability floor (so the easiest threshold no
     longer wins just because it is easiest);
  3. prefers the FEWEST legs that reach the tier's odds band, with tighter
     per-tier leg caps;
  4. only then compares weakest-leg probability, evidence quality, market
     relevance and the remaining tiebreaks.
Nothing here fabricates a joint probability: player-only multis state that
no validated joint model exists, and the option carries the price-implied
probability of its own combined odds as a plain arithmetic reference.

Two modes, selectable independently of tier (see MODE_HIGH_PROBABILITY /
MODE_VALUE), sourced from two DIFFERENT candidate pools built from that
same underlying data:
  - High Probability (the default): built from _all_alternate_legs - EVERY
    valid fresh alternate threshold line - then narrowed per player-market
    to the most demanding lines that clear the tier's probability floor
    (see above). A leg like "82% model probability, $1.25, modest edge" is
    exactly what this mode is FOR, not something to filter out.
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

import math
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
# when they already reach the target band.
#
# Caps tightened from 5/6/7/8 (multi-builder-selection-refinement). Why: every
# leg is a separate way for the whole multi to lose, and every leg carries its
# own bookmaker margin, so for a FIXED combined-odds band a multi built from
# more, shorter-priced legs is not safer than one built from fewer legs - it
# just has more failure points. The audit's illustration (independence
# arithmetic, explanation only, never a joint model): five 80% legs land
# together only ~33% of the time. The new caps keep every tier reachable from
# legs that clear that tier's own probability floor (checked against real
# stored markets in the audit) while removing the 6-8 leg "safe-looking"
# constructions the old caps allowed.
MIN_LEGS: dict[str, int] = {TIER_CONSERVATIVE: 2, TIER_BALANCED: 2, TIER_HIGHER_RETURN: 3, TIER_LONGER_SHOT: 3}
MAX_LEGS: dict[str, int] = {TIER_CONSERVATIVE: 3, TIER_BALANCED: 4, TIER_HIGHER_RETURN: 5, TIER_LONGER_SHOT: 6}

# --- Market relevance (a transparent PROXY, not popularity) ---------------
# The only prominence-like information this application actually stores is
# how many bookmakers quote a player's exact market. Across every stored
# match the standard ladder rungs (10+/15+/20+/25+ disposals, 1+ goals) are
# listed by most of the match's bookmakers, whereas in-between alternate
# lines are listed by roughly half or fewer (see the audit's coverage
# table). A leg is therefore graded "main" when at least this share of the
# match's bookmakers offer it, and "thin" otherwise. This is a statement
# about how widely a line is OFFERED - it is not liquidity, bet volume or
# customer popularity, none of which we have, and it says nothing about
# whether the model is more accurate on such lines (the audit found no such
# difference).
MAIN_MARKET_MIN_COVERAGE_SHARE = 0.5
# With only a couple of bookmakers quoting a match, "offered by most of them"
# says nothing - so below this many bookmakers the grade is not applied (every
# leg is treated as main, and the record says the signal was not informative)
# rather than filtering on evidence we do not have.
MIN_BOOKMAKERS_FOR_RELEVANCE = 4
GRADE_MAIN = "main"
GRADE_THIN = "thin"
# Coarse bands used only to break near-ties between combinations - see
# _combo_rank_key. HIGH: offered by (nearly) every bookmaker.
RELEVANCE_HIGH_SHARE = 0.85
# Relevance may only decide between combinations whose weakest legs are within
# this many probability points of each other; a bigger probability advantage
# always wins. This is what stops the relevance signal overpowering genuinely
# stronger evidence.
PROBABILITY_TIE_BAND = 0.05
# Per player-market, how many of the most demanding qualifying lines to keep.
LINES_PER_FAMILY = 2
_SEARCH_POOL_CAP = 60  # safety bound on candidate legs per bookmaker/tier/stage

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
REASON_STRONG_CANDIDATE = "STRONG_CANDIDATE"
# Only ever attached when the promoted model's calibration was genuinely
# evaluated at THIS leg's own threshold (see _calibration_checked_at_threshold)
# - previously the "strong candidate" label reused this code, which claimed a
# calibration check that was not being made.
REASON_WELL_CALIBRATED_THRESHOLD = "WELL_CALIBRATED_THRESHOLD"
REASON_MARKET_COVERAGE = "MARKET_COVERAGE"
REASON_FRESH_MARKET = "FRESH_MARKET"
REASON_POSITIVE_MODEL_EDGE = "POSITIVE_MODEL_EDGE"
REASON_GOOD_CONFIDENCE = "GOOD_CONFIDENCE"
REASON_MULTIPLE_BOOKMAKERS = "MULTIPLE_BOOKMAKERS"
REASON_PASSES_INTEGRITY_CHECKS = "PASSES_INTEGRITY_CHECKS"

WARNING_TEAMS_NOT_CONFIRMED = "TEAMS_NOT_CONFIRMED"
WARNING_RECENT_USAGE_REGIME_CHANGE = "RECENT_USAGE_REGIME_CHANGE"
WARNING_LOWER_CONFIDENCE = "LOWER_CONFIDENCE"
WARNING_SINGLE_BOOK_MARKET = "SINGLE_BOOK_MARKET"
WARNING_LESS_COMMON_LINE = "LESS_COMMON_LINE"
WARNING_CALIBRATION_NOT_AT_THRESHOLD = "CALIBRATION_NOT_AT_THRESHOLD"


def _calibration_checked_at_threshold(leg: dict) -> bool:
    """True only when calibration was evaluated at exactly this leg's
    threshold. historical_calibration_metrics maps ANY threshold to its
    NEAREST evaluated one (20/25/30/35 for disposals), so a 10+ leg carries a
    calibration record that actually describes 20+ - not evidence about 10+."""
    cal = leg.get("calibration")
    return bool(cal) and leg.get("threshold") is not None and float(cal.get("evaluated_threshold", -1)) == float(leg["threshold"])


def _reasons_for(leg: dict) -> list[dict]:
    reasons = []
    if leg["quality_tier"]["tier"] == "strong_candidate":
        reasons.append({"code": REASON_STRONG_CANDIDATE, "label": "Strong candidate on its own merits"})
    if _calibration_checked_at_threshold(leg):
        reasons.append({"code": REASON_WELL_CALIBRATED_THRESHOLD, "label": f"Calibration checked at {leg['threshold']:g}+ (ECE {leg['calibration']['ece']:.3f})"})
    rel = leg.get("market_relevance")
    if rel and rel.get("coverage_share") is not None and rel["grade"] == GRADE_MAIN and rel.get("informative"):
        reasons.append({"code": REASON_MARKET_COVERAGE, "label": f"Offered by {rel['bookmakers_offering']} of {rel['bookmakers_in_match']} bookmakers"})
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
    rel = leg.get("market_relevance")
    if rel and rel["grade"] == GRADE_THIN and rel.get("coverage_share") is not None:
        warnings.append({
            "code": WARNING_LESS_COMMON_LINE,
            "label": f"Less common line — offered by {rel['bookmakers_offering']} of {rel['bookmakers_in_match']} bookmakers.",
        })
    cal = leg.get("calibration")
    if cal and not _calibration_checked_at_threshold(leg):
        warnings.append({
            "code": WARNING_CALIBRATION_NOT_AT_THRESHOLD,
            "label": f"Model calibration was not evaluated at {leg['threshold']:g}+ (nearest evaluated: {cal['evaluated_threshold']:g}+).",
        })
    return warnings


def bookmaker_universe(match_legs: list[dict]) -> int:
    """How many distinct bookmakers quote ANY player prop for this match -
    the denominator for a leg's coverage share."""
    names: set[str] = set()
    for leg in match_legs:
        if leg["opportunity_type"] == "player":
            for entry in leg.get("bookmakers", []):
                names.add(entry["bookmaker_name"])
    return len(names)


def market_relevance(leg: dict, universe: int) -> dict:
    """Transparent, evidence-only relevance of one leg's market.

    `coverage_share` = bookmakers quoting this exact market / bookmakers
    quoting any player prop in the match. It is a PROXY for how widely the
    line is offered (standard ladder rung vs. one-off alternate line), never
    liquidity or popularity - those are not stored anywhere. Team-market legs
    (h2h/line/total) are standard products and are graded main without a
    coverage figure. With fewer than MIN_BOOKMAKERS_FOR_RELEVANCE bookmakers
    in the match the grade is not applied (`informative` False)."""
    if leg["opportunity_type"] != "player" or universe <= 0:
        return {"grade": GRADE_MAIN, "coverage_share": None, "bookmakers_offering": leg.get("n_bookmakers"), "bookmakers_in_match": universe or None, "is_proxy": True, "informative": False}
    offering = len(leg.get("bookmakers", [])) or int(leg.get("n_bookmakers", 0))
    share = min(offering / universe, 1.0)
    informative = universe >= MIN_BOOKMAKERS_FOR_RELEVANCE
    grade = GRADE_MAIN if (not informative or share >= MAIN_MARKET_MIN_COVERAGE_SHARE) else GRADE_THIN
    return {
        "grade": grade, "coverage_share": share, "bookmakers_offering": offering, "bookmakers_in_match": universe,
        "is_proxy": True, "informative": informative,
    }


def annotate_market_relevance(match_legs: list[dict]) -> list[dict]:
    """Copies of the given legs (same match) carrying `market_relevance`."""
    universe = bookmaker_universe(match_legs)
    return [{**leg, "market_relevance": market_relevance(leg, universe)} for leg in match_legs]


def _is_main_market(leg: dict) -> bool:
    rel = leg.get("market_relevance")
    return rel is None or rel["grade"] == GRADE_MAIN


def _relevance_share(leg: dict) -> float:
    rel = leg.get("market_relevance")
    if rel is None or rel.get("coverage_share") is None:
        return 1.0
    return rel["coverage_share"]


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
    universe = bookmaker_universe([o for o in raw if o["match_id"] == match_id])
    match_legs = [
        {**o, "market_relevance": market_relevance(o, universe)}
        for o in raw if o["match_id"] == match_id and o["quality_tier"]["tier"] != TIER_DO_NOT_HEADLINE
    ]
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
    universe = bookmaker_universe([o for o in raw if o["match_id"] == match_id])
    result = []
    for o in raw:
        if o["match_id"] != match_id or o["quality_tier"]["tier"] == TIER_DO_NOT_HEADLINE:
            continue
        leg = dict(o)
        leg["_family_key"] = family_key(o)
        leg["market_relevance"] = market_relevance(o, universe)
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


def _needed_value(leg: dict) -> int | None:
    """Smallest whole-number stat that satisfies the leg ("9.5+" needs 10;
    a multi_plus 1.0 goals leg needs 1) - the same equivalence used to
    recognise that "over 0.5 goals" and "1+ goals" describe one event."""
    threshold = leg.get("threshold")
    if threshold is None:
        return None
    return math.ceil(threshold) if leg.get("line_type") == "multi_plus" else math.floor(threshold) + 1


def _most_demanding_lines(pool: list[dict], per_family: int = LINES_PER_FAMILY) -> list[dict]:
    """Per player-market, keep only the `per_family` most demanding lines
    among the legs that ALREADY cleared this tier's own probability floor.

    This is what stops "the easiest threshold" from winning simply because it
    is the easiest: a player whose 15+ line clears the tier's floor is offered
    at 15+ (and the next line down), never at 10+ - while a player whose only
    qualifying line IS 10+ still gets it. Nothing is banned; the easiest line
    is simply no longer preferred when a more meaningful one qualifies. Team
    legs are left untouched."""
    by_family: dict[tuple, list[dict]] = {}
    for leg in pool:
        by_family.setdefault(leg.get("_family_key") or ("solo", _leg_id(leg)), []).append(leg)
    kept: list[dict] = []
    for legs in by_family.values():
        if legs[0]["opportunity_type"] != "player":
            kept.extend(legs)
            continue
        best_per_event: dict[int | None, dict] = {}
        for leg in legs:  # same event via two line shapes (over 0.5 / 1+): keep the better price
            need = _needed_value(leg)
            cur = best_per_event.get(need)
            if cur is None or leg["bookmaker_price"] > cur["bookmaker_price"]:
                best_per_event[need] = leg
        ordered = sorted(best_per_event.items(), key=lambda kv: (kv[0] is not None, kv[0] or 0), reverse=True)
        kept.extend(leg for _, leg in ordered[:per_family])
    return kept


def _candidate_pool(legs: list[dict], tier_key: str, mode: str, *, confirmed_only: bool, main_only: bool = False) -> list[dict]:
    pool = legs
    if confirmed_only:
        pool = [leg for leg in pool if leg["opportunity_type"] != "player" or leg.get("is_confirmed")]
    if main_only:
        pool = [leg for leg in pool if _is_main_market(leg)]
    if mode == MODE_HIGH_PROBABILITY:
        min_prob = MIN_LEG_PROBABILITY[tier_key]
        pool = [
            leg for leg in pool
            if leg["model_probability"] >= min_prob and leg["difference_pp"] >= MIN_EDGE_FLOOR_HIGH_PROBABILITY
            and not _is_strict_goals_excluded(leg, tier_key)
        ]
        pool = _most_demanding_lines(pool)
        # Ordering only decides which legs survive the safety cap below - the
        # combination search itself is exhaustive over the survivors.
        pool = sorted(pool, key=_high_probability_score, reverse=True)
        return pool[:_SEARCH_POOL_CAP]
    pool = [leg for leg in pool if leg["model_probability"] >= MIN_LEG_PROBABILITY_VALUE]
    pool = sorted(pool, key=representative_score, reverse=True)
    return pool[:_MAX_POOL_SIZE]


def _is_strong_pair(a: dict, b: dict) -> bool:
    category = _pair_correlation(a, b)
    return category is not None and CORRELATION_STRENGTH[category] == "strong"


def _correlation_warnings(combo: tuple[dict, ...]) -> list[str]:
    warnings: list[str] = []
    for a, b in combinations(combo, 2):
        category = _pair_correlation(a, b)
        if category is not None:
            warnings.append(f"\"{a['label']}\" {_CORRELATION_TEXT[category]} as \"{b['label']}\" — not independent, treat with caution")
    return warnings


def _relevance_band(mean_share: float) -> int:
    if mean_share >= RELEVANCE_HIGH_SHARE:
        return 2
    return 1 if mean_share >= MAIN_MARKET_MIN_COVERAGE_SHARE else 0


def _combo_rank_key(combo: tuple[dict, ...], warnings: list[str], mode: str) -> tuple:
    probs = [leg["model_probability"] for leg in combo]
    n_confirmed = sum(1 for leg in combo if leg["opportunity_type"] != "player" or leg.get("is_confirmed"))
    if mode == MODE_HIGH_PROBABILITY:
        # The target odds band is a hard filter in the search, not part of
        # this key. Preference order (each item only decides between
        # combinations tied on everything before it):
        #   1. FEWER LEGS - every leg is another way to lose and another
        #      layer of bookmaker margin, so for the same odds band the
        #      shortest construction wins. (The search also stops at the
        #      smallest feasible size, so this is enforced structurally too.)
        #   2. fewer unconfirmed players (matters only when provisional legs
        #      are allowed at all);
        #   3. weakest-leg probability, compared in PROBABILITY_TIE_BAND
        #      buckets - so nothing below can override a materially stronger
        #      weakest leg;
        #   4. market relevance (coarse coverage band, a proxy - see
        #      market_relevance) - only a tiebreak inside a probability band;
        #   5. exact weakest-leg probability, average evidence quality
        #      (confidence + calibration), fewer correlation warnings, average
        #      probability, then model-market edge and the usage-regime
        #      caution as the final minor tiebreaks.
        avg_quality = mean((leg["opportunity_components"]["confidence"] + leg["opportunity_components"]["calibration"]) / 2 for leg in combo)
        avg_edge = mean(leg["difference_pp"] for leg in combo)
        n_risk_flagged = sum(1 for leg in combo if leg.get("model_risk_flags"))
        n_unconfirmed = len(combo) - n_confirmed
        min_p = min(probs)
        relevance_band = _relevance_band(mean(_relevance_share(leg) for leg in combo))
        return (
            -len(combo), -n_unconfirmed, int(min_p / PROBABILITY_TIE_BAND), relevance_band, min_p, avg_quality,
            -len(warnings), mean(probs), avg_edge, -n_risk_flagged,
        )
    # Value mode: prioritise average model-market edge/value (the existing
    # transparent opportunity score), never raw combined odds.
    avg_value_score = mean(representative_score(leg) for leg in combo)
    return (avg_value_score, n_confirmed, -len(combo))


def _best_of_size(legs: list[dict], size: int, lo: float, hi: float | None, mode: str) -> tuple | None:
    """Best legal combination of EXACTLY `size` legs. `legs` must already be
    sorted by price, highest first: that ordering lets the odds band prune the
    search (if even repeating the current leg's price for every remaining slot
    cannot reach the band, neither can any later, lower-priced leg)."""
    best: tuple | None = None
    best_key: tuple | None = None
    chosen: list[dict] = []
    families: set[tuple] = set()
    players: set[int] = set()
    n = len(legs)

    def recurse(start: int, product: float) -> None:
        nonlocal best, best_key
        depth = len(chosen)
        if depth == size:
            if product < lo or (hi is not None and product > hi):
                return
            combo = tuple(chosen)
            warnings = _correlation_warnings(combo)
            key = _combo_rank_key(combo, warnings, mode)
            if best is None or key > best_key:
                best, best_key = (combo, product, warnings), key
            return
        remaining = size - depth
        for idx in range(start, n):
            leg = legs[idx]
            price = leg["bookmaker_price"]
            if product * price ** remaining < lo:
                break
            new_product = product * price
            if hi is not None and new_product > hi:
                continue
            family, player_id = leg["_family_key"], leg.get("player_id")
            if family in families or (player_id is not None and player_id in players):
                continue
            if any(_is_strong_pair(leg, other) for other in chosen):
                continue
            chosen.append(leg)
            families.add(family)
            if player_id is not None:
                players.add(player_id)
            recurse(idx + 1, new_product)
            chosen.pop()
            families.discard(family)
            if player_id is not None:
                players.discard(player_id)

    recurse(0, 1.0)
    return best


def _search_tier_bookmaker(
    pool: list[dict], lo: float, hi: float | None, min_legs: int, max_legs: int, mode: str,
    exclude_ids: set[tuple], exclude_family_keys: set[tuple], player_counts: dict[int, int],
) -> tuple | None:
    """Searches real COMBINATIONS (not a greedy top-N) from `pool`, one
    bookmaker's legs. High Probability mode returns the best combination at
    the SMALLEST size that can reach the odds band - fewer legs is the first
    preference (see _combo_rank_key); Value mode compares every size."""
    legs = [
        leg for leg in pool
        if _leg_id(leg) not in exclude_ids and leg["_family_key"] not in exclude_family_keys
        and (leg.get("player_id") is None or player_counts.get(leg["player_id"], 0) < MAX_APPEARANCES_PER_PLAYER)
    ]
    legs.sort(key=lambda leg: leg["bookmaker_price"], reverse=True)
    best = None
    best_key = None
    for size in range(min_legs, min(max_legs, len(legs)) + 1):
        found = _best_of_size(legs, size, lo, hi, mode)
        if found is None:
            continue
        if mode == MODE_HIGH_PROBABILITY:
            return found
        key = _combo_rank_key(found[0], found[2], mode)
        if best is None or key > best_key:
            best, best_key = found, key
    return best


@dataclass(frozen=True)
class TierResult:
    options: list[dict]
    unavailable_reason: str | None
    bookmaker_comparison: list[dict]  # item 9: every bookmaker's own best achievable combo for this tier, not just the winner


def _preference_stages(mode: str, confirmed_only: bool, main_only: bool) -> list[tuple[bool, bool]]:
    """(restrict_to_confirmed, restrict_to_main_markets) pools to try, most
    preferred first. A preference is only relaxed when the stricter pool
    cannot fill the tier - and only when the user allowed that relaxation
    (confirmed_only / main_only False). Value mode ranks purely on value and
    uses the single pool the user asked for."""
    if mode != MODE_HIGH_PROBABILITY:
        return [(confirmed_only, main_only)]
    stages = [(True, True)]
    if not confirmed_only:
        stages.append((False, True))
    if not main_only:
        stages.append((True, False))
        if not confirmed_only:
            stages.append((False, False))
    return stages


def _empty_tier_reason(
    legs_by_bookmaker: dict[str, list[dict]], tier_key: str, mode: str, *, confirmed_only: bool, main_only: bool,
) -> str:
    """Why a tier came up empty, distinguishing causes that call for
    different user actions."""
    lo, hi = TIER_RANGES[tier_key]
    min_legs, max_legs = MIN_LEGS[tier_key], MAX_LEGS[tier_key]

    def ready(conf: bool, main: bool) -> bool:
        return any(
            len(_candidate_pool(legs, tier_key, mode, confirmed_only=conf, main_only=main)) >= min_legs
            for legs in legs_by_bookmaker.values()
        )

    if ready(confirmed_only, main_only):
        return (
            f"Enough {TIER_LABELS[tier_key].lower()}-eligible legs exist, but none combine to reach the "
            f"${lo:.2f}{f'-${hi:.2f}' if hi else '+'} odds band using at most {max_legs} legs from a single bookmaker "
            "without breaking a correlation rule."
        )
    if confirmed_only and ready(False, main_only):
        return (
            f"Enough legs exist for a {TIER_LABELS[tier_key].lower()} multi, but not enough of them belong to "
            "CONFIRMED players — this usually means one team's lineup isn't confirmed yet, even though the "
            "other team's is. Confirm the remaining team, or turn off \"Confirmed players only\" to see it "
            "as provisional."
        )
    if main_only and ready(confirmed_only, False):
        return (
            f"Not enough main-market legs (lines offered by most bookmakers) for a {TIER_LABELS[tier_key].lower()} multi. "
            "Turn off \"Main markets only\" to allow less common lines."
        )
    min_prob = MIN_LEG_PROBABILITY.get(tier_key) if mode == MODE_HIGH_PROBABILITY else MIN_LEG_PROBABILITY_VALUE
    if mode == MODE_HIGH_PROBABILITY:
        return (
            f"No {TIER_LABELS[tier_key]} multi currently meets the required individual-leg probabilities "
            f"(needs at least {min_legs} legs each {min_prob * 100:.0f}%+, from the same bookmaker)."
        )
    return f"No {TIER_LABELS[tier_key]} multi currently has enough sensible-probability value legs from a single bookmaker."


def _selection_note(tier_key: str, mode: str, n_legs: int) -> str:
    if mode == MODE_HIGH_PROBABILITY:
        floor = MIN_LEG_PROBABILITY[tier_key]
        return (
            f"Each player leg is the highest line for that player that still clears this tier's {floor * 100:.0f}% model-probability "
            f"floor, and this is the shortest construction ({n_legs} legs, cap {MAX_LEGS[tier_key]}) that reaches the tier's odds band."
        )
    return f"Legs are ranked by model-vs-market value; at most {MAX_LEGS[tier_key]} legs for this tier."


def _options_for_tier(
    legs_by_bookmaker: dict[str, list[dict]], tier_key: str, mode: str, *, confirmed_only: bool, player_counts: dict[int, int],
    main_only: bool = True,
) -> TierResult:
    lo, hi = TIER_RANGES[tier_key]
    min_legs, max_legs = MIN_LEGS[tier_key], MAX_LEGS[tier_key]
    stages = _preference_stages(mode, confirmed_only, main_only)

    stage_ranked: dict[tuple[bool, bool], list[tuple[str, list[dict]]]] = {}
    for stage in stages:
        restrict_conf, restrict_main = stage
        pools = {
            name: _candidate_pool(legs, tier_key, mode, confirmed_only=restrict_conf, main_only=restrict_main)
            for name, legs in legs_by_bookmaker.items()
        }
        stage_ranked[stage] = sorted(
            ((name, pool) for name, pool in pools.items() if len(pool) >= min_legs), key=lambda kv: len(kv[1]), reverse=True,
        )[:_MAX_BOOKMAKERS_SEARCHED]

    if not any(stage_ranked.values()):
        return TierResult(
            options=[], unavailable_reason=_empty_tier_reason(legs_by_bookmaker, tier_key, mode, confirmed_only=confirmed_only, main_only=main_only),
            bookmaker_comparison=[],
        )

    options: list[dict] = []
    bookmaker_comparison: list[dict] = []
    exclude_ids: set[tuple] = set()
    exclude_family_keys: set[tuple] = set()
    for pass_index in range(MAX_OPTIONS_PER_TIER):
        best_overall = None
        best_overall_key = None
        best_bookmaker = None
        for stage in stages:
            for bookmaker_name, pool in stage_ranked[stage]:
                found = _search_tier_bookmaker(pool, lo, hi, min_legs, max_legs, mode, exclude_ids, exclude_family_keys, player_counts)
                if pass_index == 0 and found is not None:
                    # Item 9: capture every bookmaker's OWN best combo on this
                    # first pass (before any leg exclusions from chosen options
                    # apply), so "which bookmaker offers the best indicative
                    # combination" can be answered even for bookmakers that
                    # don't end up winning. Only the stage that actually
                    # produces the option contributes, keeping the comparison
                    # like-for-like.
                    bookmaker_comparison.append({"bookmaker": bookmaker_name, "indicative_combined_odds": found[1], "n_legs": len(found[0]), "_stage": stage})
                if found is None:
                    continue
                combo, combined_odds, warnings = found
                key = _combo_rank_key(combo, warnings, mode)
                if best_overall is None or key > best_overall_key:
                    best_overall, best_overall_key, best_bookmaker = (combo, combined_odds, warnings), key, bookmaker_name
            if best_overall is not None:
                break  # a stricter preference stage filled this option - never mix in a relaxed stage
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
            "includes_less_common_lines": any(not _is_main_market(leg) for leg in combo),
            "bookmaker": best_bookmaker,
            "mode": mode,
            "tier": tier_key,
            "lowest_leg_probability": min(leg["model_probability"] for leg in combo),
            "average_leg_probability": mean(leg["model_probability"] for leg in combo),
            "selection_note": _selection_note(tier_key, mode, len(combo)),
        }
        options.append(option)
        exclude_ids |= {_leg_id(leg) for leg in combo}
        exclude_family_keys |= {leg["_family_key"] for leg in combo}

    for i, opt in enumerate(options):
        opt["option_label"] = f"Option {chr(65 + i)}"

    unavailable_reason = None
    if not options:
        unavailable_reason = _empty_tier_reason(legs_by_bookmaker, tier_key, mode, confirmed_only=confirmed_only, main_only=main_only)
    if options:
        winning_stage = next((c["_stage"] for c in bookmaker_comparison), None)
        bookmaker_comparison = [{k: v for k, v in c.items() if k != "_stage"} for c in bookmaker_comparison if c["_stage"] == winning_stage]
    bookmaker_comparison.sort(key=lambda c: c["indicative_combined_odds"])
    return TierResult(options=options, unavailable_reason=unavailable_reason, bookmaker_comparison=bookmaker_comparison)


@dataclass(frozen=True)
class MatchMultiTiers:
    match_id: int
    n_eligible_legs: int
    bookmakers_available: list[str]
    tiers: dict[str, TierResult] = field(default_factory=dict)
    main_markets_only: bool = True
    n_main_market_legs: int = 0
    bookmakers_in_match: int = 0


def build_match_multis(
    db: Session, match_id: int, *, confirmed_only: bool = True, mode: str = DEFAULT_MODE, raw_opportunities: list[dict] | None = None,
    main_markets_only: bool = True,
) -> MatchMultiTiers:
    # High-Probability mode sources from EVERY alternate threshold line
    # (see _all_alternate_legs) and narrows it per player-market to the most
    # demanding lines that clear each tier's floor; Value mode keeps the
    # collapsed one-representative-plus-safest-alternate pool (_match_legs).
    # `main_markets_only` (default True) restricts both to lines offered by
    # most of the match's bookmakers (see market_relevance - a proxy for how
    # widely a line is offered, not popularity); High Probability mode with
    # it False still PREFERS main-market legs and only uses others when a
    # tier cannot otherwise be filled.
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
        tiers[tier_key] = _options_for_tier(
            by_bookmaker, tier_key, mode, confirmed_only=confirmed_only, player_counts=player_counts, main_only=main_markets_only,
        )

    return MatchMultiTiers(
        match_id=match_id, n_eligible_legs=len(legs),
        bookmakers_available=sorted(name for name, quotes in by_bookmaker.items() if len(quotes) >= 2),
        tiers=tiers, main_markets_only=main_markets_only,
        n_main_market_legs=sum(1 for leg in legs if _is_main_market(leg)),
        bookmakers_in_match=next((leg["market_relevance"]["bookmakers_in_match"] or 0 for leg in legs if leg["opportunity_type"] == "player"), 0),
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
        "includes_less_common_lines": opt.get("includes_less_common_lines", False),
        "selection_note": opt.get("selection_note", ""),
        # Arithmetic on the option's OWN price, not a model output: what the
        # combined odds imply before bookmaker margin. It is here so that a
        # multi of several individually likely legs is never read as being
        # about as likely as any one of them.
        "price_implied_probability": 1.0 / opt["indicative_combined_odds"],
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
                "calibration_checked_at_threshold": _calibration_checked_at_threshold(leg),
                "market_relevance": leg.get("market_relevance"),
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


def _joint_probability_note(option_dict: dict) -> str:
    n = option_dict["n_legs"]
    implied = option_dict["price_implied_probability"]
    if option_dict["same_game_pricing"] is not None:
        return (
            f"All {n} legs must hit. A validated joint model prices this combination (see Same Game Pricing); "
            f"the combined odds imply about {implied * 100:.0f}% before bookmaker margin."
        )
    illustration = 1.0
    for leg in option_dict["legs"]:
        illustration *= leg["model_probability"]
    return (
        f"All {n} legs must hit, and no validated joint-probability model covers this combination, so none is shown. "
        f"The combined odds imply about {implied * 100:.0f}% before bookmaker margin. For illustration only: legs this likely "
        f"would land together about {illustration * 100:.0f}% of the time if they were independent - not a model output."
    )


def _option_with_sgm_pricing(db: Session, match_id: int, opt: dict) -> dict:
    option_dict = option_as_dict(opt)
    option_dict["same_game_pricing"] = _try_price_same_game(db, match_id, option_dict)
    option_dict["joint_probability_note"] = _joint_probability_note(option_dict)
    return option_dict


def match_multi_tiers_as_dict(db: Session, result: MatchMultiTiers) -> dict:
    return {
        "match_id": result.match_id,
        "n_eligible_legs": result.n_eligible_legs,
        "bookmakers_available": result.bookmakers_available,
        "main_markets_only": result.main_markets_only,
        "n_main_market_legs": result.n_main_market_legs,
        "bookmakers_in_match": result.bookmakers_in_match,
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
