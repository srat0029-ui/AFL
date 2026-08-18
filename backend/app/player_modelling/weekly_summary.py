"""Weekly round-level summary stats (Sections 10, 19 of the diversification
stage) — computed from real current data, never a fixed/hardcoded
bookmaker list (Section 10: "If Ladbrokes/etc. appear later, include
dynamically. Do not hardcode three books.").
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PlayerPropMarket
from app.player_modelling.opportunity_families import price_advantage_pct as _price_advantage_pct
from app.player_modelling.upcoming_features import load_next_upcoming_round


def compute_weekly_summary(round_number: int | None, gated_opportunities: list[dict]) -> dict:
    """`gated_opportunities` is the FULL quality-gated raw universe (every
    market that currently passes the active gates), not just the
    diversified Top N shown — Section 19 wants "N opportunities pass
    current quality gates" to describe the whole gated set."""
    unique_players = {o["player_id"] for o in gated_opportunities if o.get("player_id") is not None}
    unique_matches = {o["match_id"] for o in gated_opportunities}
    bookmakers = {b["bookmaker_name"] for o in gated_opportunities for b in o["bookmakers"]}

    differences = [o["difference_pp"] for o in gated_opportunities]
    price_advantages = [pa for o in gated_opportunities if (pa := _price_advantage_pct(o)) is not None]

    return {
        "round_number": round_number,
        "n_opportunities_passing_gates": len(gated_opportunities),
        "n_unique_players": len(unique_players),
        "n_unique_matches": len(unique_matches),
        "n_bookmakers": len(bookmakers),
        "best_difference_pp": max(differences, default=None),
        "best_price_advantage_pct": max(price_advantages, default=None),
    }


def bookmaker_coverage_summary(db: Session) -> list[dict]:
    """Section 10: per-bookmaker active player-market and match counts for
    the current upcoming round, discovered from real stored quotes."""
    upcoming = load_next_upcoming_round(db)
    match_ids = {m.match_id for m in upcoming}
    if not match_ids:
        return []

    quotes = db.scalars(select(PlayerPropMarket).where(PlayerPropMarket.match_id.in_(match_ids))).all()
    per_bookmaker_markets: dict[str, set] = defaultdict(set)
    per_bookmaker_matches: dict[str, set] = defaultdict(set)
    for q in quotes:
        name = q.bookmaker.name
        per_bookmaker_markets[name].add((q.match_id, q.player_id, q.market_type, q.line_type, q.threshold))
        per_bookmaker_matches[name].add(q.match_id)

    return [
        {"bookmaker_name": name, "n_active_player_markets": len(markets), "n_matches_covered": len(per_bookmaker_matches[name])}
        for name, markets in sorted(per_bookmaker_markets.items(), key=lambda kv: -len(kv[1]))
    ]
