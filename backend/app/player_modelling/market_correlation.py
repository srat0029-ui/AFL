"""Cross-market correlation families (Market Integrity + Final Weekly
Picks stage, Section 6) — extends opportunity_families.py's alternate-line
grouping (same player/team + match + market family) to relationships
BETWEEN different market families in the same match. The real motivating
case: Collingwood H2H + Collingwood +24.5 are two different market
families (h2h vs line) under the existing grouping, so they can both
headline Best Opportunities today as if they were independent views, when
they are really one underlying opinion ("Collingwood performs well")
expressed two ways.

Deliberately simple and rule-based per the brief's own instruction ("do
not over-engineer correlation estimates yet") — three named categories,
no statistical/estimated correlation coefficients:

- STRONG: team H2H + team line, same team, same match. Same underlying
  directional view on one team.
- MODERATE: a team H2H/line opinion + a match total opinion in the same
  match. Related by shared game-environment, but a genuinely different
  axis of opinion (not "the same bet twice").
- MODERATE: two different players on the SAME team in the SAME match.
  Share game-environment correlation (e.g. a high-scoring game lifts
  both), but are still different players/models.
"""

from collections import defaultdict
from itertools import combinations

CORRELATION_TEAM_DIRECTIONAL = "same_team_directional_view"
CORRELATION_TEAM_AND_TOTAL = "team_view_and_match_total"
CORRELATION_SAME_TEAM_PLAYERS = "same_team_players"

CORRELATION_STRENGTH = {
    CORRELATION_TEAM_DIRECTIONAL: "strong",
    CORRELATION_TEAM_AND_TOTAL: "moderate",
    CORRELATION_SAME_TEAM_PLAYERS: "moderate",
}


def _pair_correlation(a: dict, b: dict) -> str | None:
    if a["match_id"] != b["match_id"]:
        return None

    if a["opportunity_type"] == "team" and b["opportunity_type"] == "team":
        a_directional = a["market_type"] in ("h2h", "line") and a.get("team_id") is not None
        b_directional = b["market_type"] in ("h2h", "line") and b.get("team_id") is not None
        if a_directional and b_directional and a["team_id"] == b["team_id"] and a["market_type"] != b["market_type"]:
            return CORRELATION_TEAM_DIRECTIONAL
        if a_directional and b["market_type"] == "total":
            return CORRELATION_TEAM_AND_TOTAL
        if b_directional and a["market_type"] == "total":
            return CORRELATION_TEAM_AND_TOTAL
        return None

    if a["opportunity_type"] == "player" and b["opportunity_type"] == "player":
        if a["player_id"] == b["player_id"]:
            return None  # same-player alternate lines are already one family, not a cross-market pair
        if a.get("team_id") is not None and a.get("team_id") == b.get("team_id"):
            return CORRELATION_SAME_TEAM_PLAYERS
        return None

    return None


def strong_correlation_group_key(o: dict) -> tuple | None:
    """Section 7's Final Weekly Shortlist collapsing key: opportunities
    sharing this key express the same underlying team-directional view
    (H2H and line for the same team) and should count as ONE opinion, not
    two, on the Shortlist. Returns None for opportunities that aren't part
    of a strong-correlation group (totals, players — those use the
    softer, descriptive labels below only, never a hard collapse)."""
    if o["opportunity_type"] == "team" and o["market_type"] in ("h2h", "line") and o.get("team_id") is not None:
        return ("team_directional", o["match_id"], o["team_id"])
    return None


def _identity(o: dict) -> tuple:
    return (o["opportunity_type"], o["match_id"], o.get("player_id"), o["market_type"], o.get("selection"), o.get("line_value"), o.get("threshold"))


def compute_market_correlations(opportunities: list[dict]) -> dict[tuple, list[dict]]:
    """For a SELECTED list of opportunities (already diversified — matching
    opportunity_families.compute_correlation_labels's own convention of
    only describing correlation relative to what's actually being shown),
    returns opportunity-identity-tuple -> list of {other_label, category,
    strength} records describing every cross-market correlation it has
    with another opportunity IN THE SAME LIST."""
    result: dict[tuple, list[dict]] = defaultdict(list)
    for a, b in combinations(opportunities, 2):
        category = _pair_correlation(a, b)
        if category is None:
            continue
        strength = CORRELATION_STRENGTH[category]
        result[_identity(a)].append({"other_label": b["label"], "category": category, "strength": strength})
        result[_identity(b)].append({"other_label": a["label"], "category": category, "strength": strength})
    return result


def market_correlation_labels(opportunity: dict, correlations_by_identity: dict[tuple, list[dict]]) -> list[str]:
    records = correlations_by_identity.get(_identity(opportunity), [])
    labels = []
    for r in records:
        if r["category"] == CORRELATION_TEAM_DIRECTIONAL:
            labels.append(f"Same underlying view as \"{r['other_label']}\" (strongly related)")
        elif r["category"] == CORRELATION_TEAM_AND_TOTAL:
            labels.append(f"Shares game environment with \"{r['other_label']}\" (moderately related)")
        elif r["category"] == CORRELATION_SAME_TEAM_PLAYERS:
            labels.append(f"Same team as \"{r['other_label']}\" (moderately related)")
    return labels