"""Real-data verification script for the Finals Multi Quality + Match-Day
Readiness stage (item 18). Read-only. Run: python -m scripts.finals_multi_quality_verification
"""

from collections import Counter

from app.database import SessionLocal
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.match_readiness import compute_match_readiness
from app.player_modelling.multi_builder import MODE_HIGH_PROBABILITY, MODE_VALUE, TIER_ORDER, build_match_multis
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.models import Match
from app.models.bookmaker import ELIGIBILITY_INCLUDED

PROB_CUTS = [0.50, 0.60, 0.65, 0.70, 0.75, 0.80, 0.90]

db = SessionLocal()
try:
    upcoming = load_next_upcoming_round(db)
    print(f"upcoming finals matches: {len(upcoming)}")
    if not upcoming:
        print("No upcoming matches found at all - nothing to audit.")
    match_ids = {m.match_id for m in upcoming}

    raw = load_best_opportunities(
        db, market_scope="all", include_uncertain=True, include_stale=True, include_insufficient_history=True, limit=None,
    )

    for m in upcoming:
        match = db.get(Match, m.match_id)
        legs = [r for r in raw if r["match_id"] == m.match_id]
        print(f"\n=== {match.home_team.name} v {match.away_team.name} (match_id={m.match_id}) ===")

        fresh = [l for l in legs if l["odds_freshness"] != "stale"]
        valid_priced = [l for l in legs if l["quality_tier"]["tier"] != "do_not_headline"]
        confirmed_player = [l for l in legs if l["opportunity_type"] == "player" and l.get("is_confirmed")]
        provisional_player = [l for l in legs if l["opportunity_type"] == "player" and not l.get("is_confirmed")]
        by_market = Counter(l["market_type"] for l in legs)
        by_bookmaker = Counter(b["bookmaker_name"] for l in legs for b in l.get("bookmakers", []) if b.get("eligibility") == ELIGIBILITY_INCLUDED)

        print(f"  total raw legs (incl. stale): {len(legs)}")
        print(f"  fresh bookmaker legs: {len(fresh)}")
        print(f"  valid (non-do-not-headline) legs: {len(valid_priced)}")
        print(f"  confirmed-player legs: {len(confirmed_player)}")
        print(f"  provisional-player legs: {len(provisional_player)}")
        print(f"  disposals: {by_market.get('player_disposals', 0)}  goals: {by_market.get('player_goals', 0)}  "
              f"h2h: {by_market.get('h2h', 0)}  line: {by_market.get('line', 0)}  total: {by_market.get('total', 0)}")
        print(f"  candidate legs by bookmaker: {dict(by_bookmaker)}")

        counts = {f">= {int(c * 100)}%": sum(1 for l in legs if l["model_probability"] >= c) for c in PROB_CUTS}
        print(f"  legs by model probability floor: {counts}")

        readiness = compute_match_readiness(db, m.match_id)
        print(f"  readiness: {readiness.state}  reasons={readiness.reasons}")

        for mode in (MODE_HIGH_PROBABILITY, MODE_VALUE):
            result = build_match_multis(db, m.match_id, confirmed_only=False, mode=mode)
            print(f"  --- mode={mode} (confirmed_only=False) ---")
            for tier_key in TIER_ORDER:
                tr = result.tiers[tier_key]
                if not tr.options:
                    print(f"    {tier_key}: none — {tr.unavailable_reason}")
                    continue
                for opt in tr.options:
                    print(
                        f"    {tier_key} [{opt['option_label']}]: {opt['n_legs']} legs, "
                        f"lowest={opt['lowest_leg_probability']:.2f} avg={opt['average_leg_probability']:.2f}, "
                        f"odds=${opt['indicative_combined_odds']:.2f}, bookmaker={opt['bookmaker']}"
                    )
finally:
    db.close()
