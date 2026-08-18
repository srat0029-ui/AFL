"""Opportunity family grouping + diversified representative selection
(Weekly Opportunity Discovery + Diversification stage).

The problem this solves: alternate threshold lines for the SAME player in
the SAME match (e.g. Darcy Gardiner 12.5+/13.5+/14.5+/.../17.5+ disposals)
are highly correlated manifestations of one underlying model projection,
not independent betting opportunities. Ranking every line individually
(still fully available — see best_opportunities.load_best_opportunities,
unchanged) let a single hot player's alternate lines fill an entire "Top
10" list. This module groups them into one `opportunity_family` per
(player-or-team, match, market family), picks the single most useful
representative line to headline, and keeps every other line as an
inspectable alternate underneath it.

Every score used here is the SAME transparent opportunity_score this app
already computes (app/player_modelling/prop_opportunity_ranking.py) —
this module only adds a documented, visible multiplier and a set of
selection/grouping RULES on top of it, never a new opaque number.
"""

from collections import defaultdict
from dataclasses import dataclass

from app.models.bookmaker import ELIGIBILITY_INCLUDED

# An almost-certain line (e.g. $1.05) or a rare-event longshot (e.g.
# $250) is rarely the "most useful" representative for a family even if
# its raw EV number looks large — see Section 3's explicit "do not
# simply select the highest model EV blindly". These thresholds are
# deliberately generous (most normal lines are $1.20-$8) so this only
# ever demotes genuine extremes, not ordinary short/long favourites.
_EXTREME_SHORT_PRICE = 1.20
_SHORT_PRICE = 1.35
_EXTREME_LONG_PRICE = 15.0
_LONG_PRICE = 8.0

MAX_HEADLINES_PER_PLAYER = 2
MAX_HEADLINES_PER_MATCH_SOFT_CAP = 3


def market_family_label(opportunity: dict) -> str:
    if opportunity["opportunity_type"] == "player":
        return "disposals" if opportunity["market_type"] == "player_disposals" else "goals"
    return opportunity["market_type"]  # "h2h" | "line" | "total"


def family_key(opportunity: dict) -> tuple:
    """At minimum groups by player + match + market family (Section 2).
    Team markets group by match + market type (all line_values/selections
    of the same underlying market, e.g. every total-points line, are one
    family) since there is no player axis for them."""
    if opportunity["opportunity_type"] == "player":
        return ("player", opportunity["match_id"], opportunity["player_id"], market_family_label(opportunity))
    return ("team", opportunity["match_id"], opportunity["market_type"])


def family_label(opportunity: dict, match_label: str) -> str:
    family = market_family_label(opportunity)
    if opportunity["opportunity_type"] == "player":
        return f"{opportunity['player_name']} / {match_label} / {family}"
    return f"{match_label} / {family}"


def _price_extremity_multiplier(price: float) -> float:
    if price < _EXTREME_SHORT_PRICE or price > _EXTREME_LONG_PRICE:
        return 0.5
    if price < _SHORT_PRICE or price > _LONG_PRICE:
        return 0.8
    return 1.0


def representative_score(opportunity: dict) -> float:
    """The existing opportunity_score (difference/EV/confidence/freshness/
    lineup/calibration, exactly as computed elsewhere), scaled by the
    visible price-extremity multiplier above. Confidence, calibration and
    freshness are already first-class components of opportunity_score, so
    this naturally favours a well-calibrated, fresh, confidently-priced
    line over a raw-EV outlier without inventing a second formula."""
    return opportunity["opportunity_score"] * _price_extremity_multiplier(opportunity["best_price"])


@dataclass(frozen=True)
class OpportunityFamily:
    key: tuple
    label: str
    representative: dict
    alternates: list[dict]  # every other line in the family, best-first


def group_into_families(opportunities: list[dict], match_labels: dict[int, str]) -> list[OpportunityFamily]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for o in opportunities:
        groups[family_key(o)].append(o)

    families = []
    for key, members in groups.items():
        members_sorted = sorted(members, key=representative_score, reverse=True)
        representative = members_sorted[0]
        alternates = members_sorted[1:]
        label = family_label(representative, match_labels.get(representative["match_id"], f"match {representative['match_id']}"))
        families.append(OpportunityFamily(key=key, label=label, representative=representative, alternates=alternates))
    return families


def diversify(families: list[OpportunityFamily], *, limit: int | None = None) -> list[OpportunityFamily]:
    """Section 4's rules: max 1 headline per family (automatic — one
    representative per family already), max 2 per player across
    families, and a soft per-match cap that only kicks in when there are
    other quality families waiting from a DIFFERENT match — never used to
    pad the list with a weaker match's opportunity just to hit a target
    count (see the backfill pass below)."""
    ranked = sorted(families, key=lambda f: representative_score(f.representative), reverse=True)

    selected: list[OpportunityFamily] = []
    per_player: dict[int, int] = defaultdict(int)
    per_match: dict[int, int] = defaultdict(int)
    deferred: list[OpportunityFamily] = []

    for fam in ranked:
        rep = fam.representative
        player_id = rep.get("player_id")
        match_id = rep["match_id"]

        if player_id is not None and per_player[player_id] >= MAX_HEADLINES_PER_PLAYER:
            continue

        if per_match[match_id] >= MAX_HEADLINES_PER_MATCH_SOFT_CAP:
            deferred.append(fam)
            continue

        selected.append(fam)
        if player_id is not None:
            per_player[player_id] += 1
        per_match[match_id] += 1
        if limit is not None and len(selected) >= limit:
            return selected

    # Backfill from the deferred (over-match-cap) pool only if the list
    # is still short of the target — a genuinely strong match shouldn't
    # be able to contribute fewer opportunities than exist just because
    # of the soft cap, once every other match's quality options are
    # exhausted.
    for fam in deferred:
        if limit is not None and len(selected) >= limit:
            break
        rep = fam.representative
        player_id = rep.get("player_id")
        if player_id is not None and per_player[player_id] >= MAX_HEADLINES_PER_PLAYER:
            continue
        selected.append(fam)
        if player_id is not None:
            per_player[player_id] += 1

    return selected


def compute_correlation_labels(selected: list[OpportunityFamily]) -> dict[tuple, list[str]]:
    """Section 5: never let two correlated lines look like independent
    opportunities. Computed relative to the final SELECTED list, not the
    full universe, since correlation with something not being shown isn't
    actionable information."""
    player_counts: dict[int, int] = defaultdict(int)
    match_counts: dict[int, int] = defaultdict(int)
    for fam in selected:
        rep = fam.representative
        if rep.get("player_id") is not None:
            player_counts[rep["player_id"]] += 1
        match_counts[rep["match_id"]] += 1

    labels: dict[tuple, list[str]] = {}
    for fam in selected:
        rep = fam.representative
        fam_labels = []
        if rep.get("player_id") is not None and player_counts[rep["player_id"]] > 1:
            fam_labels.append("Same player as another opportunity")
        if match_counts[rep["match_id"]] > 1:
            fam_labels.append("Same match as another opportunity")
        if fam.alternates:
            fam_labels.append("Highly related alternate line" if len(fam.alternates) == 1 else f"{len(fam.alternates)} highly related alternate lines")
        labels[fam.key] = fam_labels
    return labels


def price_advantage_pct(opportunity: dict) -> float | None:
    """Section 9 (original), refined by Market Integrity stage Section 4:
    how much the best-priced ELIGIBLE bookmaker beats the next-best
    ELIGIBLE one — a price-shopping signal, deliberately distinct from
    model edge (which compares the model to the market, not one bookmaker
    to another). Excludes exchange/excluded bookmakers by default so a
    structurally-different exchange back price (see
    bookmaker_classification.py — the real "Richmond to win" case, Betfair
    $34.00 vs sportsbook consensus ~$11-19) can never inflate this figure
    without disclosure; `opportunity["bookmakers"]` is expected to already
    carry `eligibility` (see bookmaker_classification.annotate_price_entries).
    Bookmakers without an `eligibility` key (not yet annotated) are treated
    as eligible, so this stays backwards compatible with any caller that
    hasn't been updated yet."""
    prices = sorted(
        (b["price_decimal"] for b in opportunity["bookmakers"] if b.get("eligibility", ELIGIBILITY_INCLUDED) == ELIGIBILITY_INCLUDED),
        reverse=True,
    )
    if len(prices) < 2:
        return None
    return (prices[0] - prices[1]) / prices[1] * 100.0
