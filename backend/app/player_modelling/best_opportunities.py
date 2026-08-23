"""Best Opportunities ranking engine (Sections 6-11, 17-18 of the best-bets
stage brief) — "What are the strongest model-vs-market opportunities
available across this AFL round right now, and which bookmaker currently
has the best price?"

Merges two ALREADY-EXISTING, unmodified sources into one transparently-
ranked list, scoped to the single next upcoming round:

- player markets: app/player_modelling/prop_insights_normalized.py's
  load_normalized_prop_insights (best-price-across-bookmakers grouping,
  devig, freshness, calibration — Section 13's "never build a second prop
  model" applies here too: this module adds zero new probability math for
  players).
- team markets: app/edges/calculator.py's compute_match_edges (the
  existing live Elo/Poisson edge calculator). Grouped across bookmakers
  the same way player markets are, since Section 7 requires a single
  "best price" headline regardless of market type. Only matches with at
  least one manually-entered OddsQuote row produce team opportunities —
  there is no automated team-market ingestion in this app (Section 17
  explicitly says not to rebuild the underlying models to fix that).

Both market types are scored with the SAME transparent, multi-component
prop_opportunity_ranking.compute_opportunity_score (Section 9: no second,
unexplained ranking formula) via a small vocabulary mapping — team-market
confidence tiers ("higher"/"moderate"/"lower"/"insufficient_data") mean
the same thing as player ones, just spelled differently by
app/edges/confidence.py, which predates this stage and is out of scope to
rename.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.calculator import ModelContext, ModelsUnavailableError, build_model_context, compute_match_edges
from app.models import Match, OddsQuote
from app.models.bookmaker import ELIGIBILITY_INCLUDED
from app.player_modelling.bookmaker_classification import annotate_price_entries, best_prices, load_bookmaker_info
from app.player_modelling.market_maturity import classify_market_maturity, hours_until, maturity_as_dict
from app.player_modelling.opportunity_explanation import why_team_edge_exists
from app.player_modelling.price_integrity import diagnose_price_spread, diagnostic_as_dict
from app.player_modelling.prop_insights_normalized import load_normalized_prop_insights
from app.player_modelling.prop_math import categorize_edge
from app.player_modelling.quality_tiers import compute_quality_tier, quality_tier_as_dict
from app.player_modelling.prop_odds_freshness import DEFAULT_THRESHOLDS, FreshnessThresholds, freshness_state
from app.player_modelling.prop_opportunity_ranking import compute_opportunity_score
from app.player_modelling.upcoming_features import load_next_upcoming_round

OPPORTUNITY_TYPE_PLAYER = "player"
OPPORTUNITY_TYPE_TEAM = "team"

# Section 10's default quality gates. "Insufficient history" doubles as the
# team-market equivalent via the vocabulary mapping below - a team market
# priced from too few games of Elo/Poisson history is exactly as
# "not-yet-trustworthy" as a player with too little game history.
_EXCLUDED_CONFIDENCE_TIERS = {"insufficient_history"}

_TEAM_CONFIDENCE_MAP = {
    "higher": "higher_confidence",
    "moderate": "moderate_confidence",
    "lower": "lower_confidence",
    "insufficient_data": "insufficient_history",
}

_MARKET_LABELS = {"player_disposals": "Disposals", "player_goals": "Goals"}


@dataclass(frozen=True)
class _TeamMarketKey:
    match_id: int
    market_type: str
    selection: str
    line_value: float | None


def _player_label(player_name: str, threshold: float, market_type: str) -> str:
    market_label = _MARKET_LABELS.get(market_type, market_type)
    threshold_display = f"{threshold:g}"
    return f"{player_name} {threshold_display}+ {market_label}"


def _resolve_team_id(match: Match, market_type: str, selection: str) -> int | None:
    """Section 6's cross-market correlation grouping needs to know WHICH
    team a h2h/line selection is directionally about (a "total" market
    isn't about either team specifically)."""
    if market_type not in ("h2h", "line"):
        return None
    if selection == match.home_team.name:
        return match.home_team_id
    if selection == match.away_team.name:
        return match.away_team_id
    return None


def _team_label(market_type: str, selection: str, line_value: float | None) -> str:
    if market_type == "h2h":
        return f"{selection} to win"
    if market_type == "line":
        sign = f"{line_value:+g}" if line_value is not None else ""
        return f"{selection} {sign}"
    if market_type == "total":
        return f"{selection} {line_value:g} total points" if line_value is not None else f"{selection} total points"
    return f"{selection} ({market_type})"


def _player_opportunities(db: Session, match_ids: set[int], *, include_uncertain: bool) -> list[dict]:
    rows = load_normalized_prop_insights(db, include_uncertain=include_uncertain, opportunities_only=True)
    results = []
    for r in rows:
        if r["match_id"] not in match_ids:
            continue
        results.append(
            {
                "opportunity_type": OPPORTUNITY_TYPE_PLAYER,
                "match_id": r["match_id"],
                "round_number": r["round_number"],
                "season_year": r["season_year"],
                "label": _player_label(r["player_name"], r["threshold"], r["market_type"]),
                "market_type": r["market_type"],
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "team_id": r["team_id"],
                "line_type": r["line_type"],
                "threshold": r["threshold"],
                "selection": None,
                "line_value": None,
                "model_probability": r["model_probability"],
                "model_fair_odds": r["model_fair_odds"],
                "best_price": r["best_price"],
                "best_bookmaker": r["best_bookmaker"],
                "best_price_is_exchange": r["best_price_is_exchange"],
                "eligible_price_available": r["eligible_price_available"],
                "best_price_all_bookmakers": r["best_price_all_bookmakers"],
                "best_bookmaker_all_bookmakers": r["best_bookmaker_all_bookmakers"],
                "best_price_all_differs_from_enabled": r["best_price_all_differs_from_enabled"],
                "price_shopping": r["price_shopping"],
                "market_implied_probability": r["raw_implied_probability"],
                "devigged_probability": r["devigged_probability"],
                "overround_removed": r["overround_removed"],
                "difference_pp": r["difference_pp"],
                "expected_value": r["expected_value"],
                "edge_category": r["edge_category"],
                "confidence_tier": r["confidence_tier"],
                "selection_status": r["selection_status"],
                "is_confirmed": r["is_confirmed"],
                "n_bookmakers": r["n_bookmakers"],
                "bookmakers": r["bookmakers"],
                "snapshot_count": r["snapshot_count"],
                "odds_freshness": r["odds_freshness"],
                "why_model_likes_it": r["why_model_likes_it"],
                "calibration": r["calibration"],
                "warnings": r["warnings"],
                "usage_regime": r["usage_regime"],
                "model_risk_flags": r["model_risk_flags"],
                "opportunity_score": r["opportunity_score"],
                "opportunity_components": r["opportunity_components"],
            }
        )
    return results


def _team_opportunities(
    db: Session, matches: list[Match], context: ModelContext, *, freshness_thresholds: FreshnessThresholds
) -> list[dict]:
    bookmaker_info = load_bookmaker_info(db)
    results = []
    for match in matches:
        edges = compute_match_edges(db, match, context)
        if not edges:
            continue

        quotes_by_id = {q.id: q for q in db.scalars(select(OddsQuote).where(OddsQuote.match_id == match.id)).all()}
        quote_recorded_at = {qid: q.recorded_at for qid, q in quotes_by_id.items()}

        grouped: dict[_TeamMarketKey, list] = defaultdict(list)
        for edge in edges:
            key = _TeamMarketKey(edge.match_id, edge.market_type, edge.selection, edge.line_value)
            grouped[key].append(edge)

        for key, group in grouped.items():
            # Market Integrity stage, Section 1/2 audit finding: OddsQuote
            # is append-only (team_odds_ingestion.py keeps every historical
            # snapshot, not just the current one), so compute_match_edges
            # returns one edge PER SNAPSHOT — a bookmaker whose price moved
            # would otherwise appear twice here, an old and a new quote
            # from the very same book compared against each other as if
            # they were two different bookmakers (this is exactly what the
            # price-integrity diagnostic below is designed to catch, and a
            # real example — "PointsBet (AU) $26 vs PointsBet (AU) $19,
            # recorded 16 hours apart" — surfaced during this stage's real
            # verification). Keep only the LATEST snapshot per bookmaker,
            # mirroring prop_insights_normalized.py's _latest_per_bookmaker.
            latest_by_bookmaker: dict[str, object] = {}
            for e in group:
                current = latest_by_bookmaker.get(e.bookmaker_name)
                if current is None or quote_recorded_at[e.odds_quote_id] > quote_recorded_at[current.odds_quote_id]:
                    latest_by_bookmaker[e.bookmaker_name] = e
            group = list(latest_by_bookmaker.values())
            group.sort(key=lambda e: e.price_decimal, reverse=True)
            bookmakers_raw = [
                {
                    "bookmaker_name": e.bookmaker_name,
                    "price_decimal": e.price_decimal,
                    "recorded_at": quote_recorded_at[e.odds_quote_id],
                    "freshness": freshness_state(quote_recorded_at[e.odds_quote_id], thresholds=freshness_thresholds),
                    "source": quotes_by_id[e.odds_quote_id].source,
                }
                for e in group
            ]
            bookmakers = annotate_price_entries(bookmakers_raw, bookmaker_info)

            # Market Integrity stage, Sections 4-5, 13: the headline "best"
            # price must come from an ELIGIBLE (sportsbook, enabled)
            # bookmaker whenever one exists — an exchange back price (e.g.
            # Betfair) is a different price product and must never silently
            # inflate the model-vs-market comparison. `bookmakers` is
            # already sorted best-price-first, so filtering preserves order.
            eligible = [b for b in bookmakers if b["eligibility"] == ELIGIBILITY_INCLUDED]
            eligible_price_available = bool(eligible)
            best_entry = (eligible or bookmakers)[0]
            price_summary = best_prices(bookmakers)

            best = next(e for e in group if e.bookmaker_name == best_entry["bookmaker_name"] and e.price_decimal == best_entry["price_decimal"])

            confidence_tier = _TEAM_CONFIDENCE_MAP.get(best.confidence_tier, best.confidence_tier)
            freshness = freshness_state(quote_recorded_at[best.odds_quote_id], thresholds=freshness_thresholds)
            warnings = list(best.confidence_reasons)
            if not best.overround_removed:
                warnings.append("Only one side of this market was quoted — the raw implied probability likely still includes bookmaker margin.")
            if not eligible_price_available:
                kind = "exchange" if best_entry["is_exchange"] else "excluded"
                warnings.append(
                    f"No enabled bookmaker currently quotes this market — showing {best_entry['bookmaker_name']} "
                    f"({kind}, informational only). Enable this bookmaker in settings to use it as a headline price."
                )
            elif best_entry["is_exchange"]:
                warnings.append(
                    f"{best_entry['bookmaker_name']} is a betting exchange — this is a back price set by other bettors, "
                    "not a fixed-odds bookmaker price, and can diverge more than a normal price-shopping gap would suggest."
                )

            opportunity = compute_opportunity_score(
                difference_pp=best.model_edge,
                expected_value=best.expected_value,
                confidence_tier=confidence_tier,
                is_uncertain_participation=False,
                freshness=freshness,
                has_calibration_data=False,
            )

            results.append(
                {
                    "opportunity_type": OPPORTUNITY_TYPE_TEAM,
                    "match_id": match.id,
                    "round_number": match.round.round_number,
                    "season_year": match.season.year,
                    "label": _team_label(key.market_type, key.selection, key.line_value),
                    "market_type": key.market_type,
                    "player_id": None,
                    "player_name": None,
                    "team_id": _resolve_team_id(match, key.market_type, key.selection),
                    "line_type": None,
                    "threshold": None,
                    "selection": key.selection,
                    "line_value": key.line_value,
                    "model_probability": best.model_probability,
                    "model_fair_odds": best.fair_odds,
                    "best_price": best.price_decimal,
                    "best_bookmaker": best.bookmaker_name,
                    "best_price_is_exchange": best_entry["is_exchange"],
                    "eligible_price_available": eligible_price_available,
                    "best_price_all_bookmakers": price_summary["best_all"]["price_decimal"] if price_summary["best_all"] else None,
                    "best_bookmaker_all_bookmakers": price_summary["best_all"]["bookmaker_name"] if price_summary["best_all"] else None,
                    "best_price_all_differs_from_enabled": price_summary["best_all_differs_from_enabled"],
                    "price_shopping": {
                        "best_enabled": price_summary["best_enabled"],
                        "next_best_enabled": price_summary["next_best_enabled"],
                        "worst_enabled": price_summary["worst_enabled"],
                    },
                    "quote_source": quotes_by_id[best.odds_quote_id].source,
                    "market_implied_probability": best.market_implied_probability,
                    "devigged_probability": best.fair_market_probability if best.overround_removed else None,
                    "overround_removed": best.overround_removed,
                    "difference_pp": best.model_edge,
                    "expected_value": best.expected_value,
                    "edge_category": categorize_edge(best.model_edge, confidence_tier),
                    "confidence_tier": confidence_tier,
                    "selection_status": None,
                    "is_confirmed": None,
                    "n_bookmakers": len(group),
                    "bookmakers": bookmakers,
                    "snapshot_count": None,  # team markets don't yet track historical price snapshots (Section 12)
                    "odds_freshness": freshness,
                    "why_model_likes_it": why_team_edge_exists(
                        best.model_probability, best.secondary_model_probability, best.fair_market_probability
                    ),
                    "calibration": None,
                    "warnings": warnings,
                    "usage_regime": None,  # team markets have no player-level usage-regime concept
                    "model_risk_flags": [],
                    "opportunity_score": opportunity.total,
                    "opportunity_components": {
                        "difference": opportunity.difference_component,
                        "expected_value": opportunity.ev_component,
                        "confidence": opportunity.confidence_component,
                        "freshness": opportunity.freshness_component,
                        "lineup": opportunity.lineup_component,
                        "calibration": opportunity.calibration_component,
                        "penalty_multiplier": opportunity.penalty_multiplier,
                        "penalty_reasons": opportunity.penalty_reasons,
                    },
                }
            )
    return results


def load_best_opportunities(
    db: Session,
    *,
    market_scope: str = "all",  # "all" | "player" | "team"
    include_uncertain: bool = False,
    include_stale: bool = False,
    include_insufficient_history: bool = False,
    limit: int | None = None,
    freshness_thresholds: FreshnessThresholds = DEFAULT_THRESHOLDS,
) -> list[dict]:
    upcoming = load_next_upcoming_round(db)
    match_ids = {m.match_id for m in upcoming}
    if not match_ids:
        return []

    results: list[dict] = []

    if market_scope in ("all", "player"):
        results.extend(_player_opportunities(db, match_ids, include_uncertain=include_uncertain))

    if market_scope in ("all", "team"):
        matches_with_quotes_ids = set(
            db.scalars(select(OddsQuote.match_id).where(OddsQuote.match_id.in_(match_ids)).distinct()).all()
        )
        if matches_with_quotes_ids:
            matches = db.scalars(select(Match).where(Match.id.in_(matches_with_quotes_ids))).all()
            try:
                context = build_model_context(db)
            except ModelsUnavailableError:
                context = None
            if context is not None:
                results.extend(_team_opportunities(db, matches, context, freshness_thresholds=freshness_thresholds))

    # Market Integrity stage, Section 2: attach (never hide) an extreme
    # eligible-vs-eligible price-difference diagnostic to every
    # opportunity it applies to. Downstream views decide whether a failed
    # diagnostic should keep an opportunity out of a HEADLINE list (see
    # final_shortlist.py); All Markets / Best Opportunities still show it.
    all_match_ids = {r["match_id"] for r in results}
    match_scheduled_start = {
        m.id: m.scheduled_start for m in (db.scalars(select(Match).where(Match.id.in_(all_match_ids))).all() if all_match_ids else [])
    }
    now = datetime.now(timezone.utc)
    for r in results:
        diag = diagnose_price_spread(r)
        r["price_integrity"] = diagnostic_as_dict(diag) if diag is not None else None

        # Section 12: descriptive-only market maturity.
        kickoff = match_scheduled_start.get(r["match_id"])
        maturity = classify_market_maturity(
            n_bookmakers=r["n_bookmakers"],
            hours_until_kickoff=hours_until(kickoff, now) if kickoff is not None else None,
            snapshot_count=r["snapshot_count"],
        )
        r["market_maturity"] = maturity_as_dict(maturity)

    # Section 9: quality/readiness tiers - computed AFTER price_integrity
    # and market_maturity are attached, since price_integrity feeds the
    # tier's hard "do not headline" gate.
    for r in results:
        tier = compute_quality_tier(r)
        r["quality_tier"] = quality_tier_as_dict(tier)

    # Section 10's default quality gates - all visible/overridable, none silent.
    if not include_insufficient_history:
        results = [r for r in results if r["confidence_tier"] not in _EXCLUDED_CONFIDENCE_TIERS]
    if not include_stale:
        results = [r for r in results if r["odds_freshness"] != "stale"]

    results.sort(key=lambda r: r["opportunity_score"], reverse=True)
    if limit is not None:
        results = results[:limit]
    return results
