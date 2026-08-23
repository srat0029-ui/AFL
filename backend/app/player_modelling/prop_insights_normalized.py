"""Best-price, multi-bookmaker Prop Insights (Sections 12-23 of the
automated-odds stage brief) — the normalized view on top of
PlayerPropMarket's append-only quote snapshots. Complements (does not
replace) app/player_modelling/live_report_query.py's load_prop_insights,
which stays as the per-quote view manual entry has always used; this
module groups quotes by NORMALIZED MARKET (match, player, market_type,
line_type, threshold) across every bookmaker and snapshot, because "which
bookmaker currently offers the best price for this exact market" is a
different question than "what is this one row's edge."

Reuses, never duplicates: model probability comes from
disposal_distribution_for/goal_distribution_for/price_line (the SAME
promoted-model distribution live_report_query.py already uses — Section
13's explicit "never create a second prop model"); confidence/lineup
gating reuses current_lineup_for/downgrade_confidence/
EXPECTED_IN_SELECTION_STATUSES; devig/EV reuse
app/player_modelling/prop_math.py's compare_model_to_market exactly as
before, just with a real opposite_side_odds argument when the best-priced
bookmaker itself offers both sides of the same snapshot (Section 15).
"""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PlayerDisposalProjection, PlayerGoalProjection, PlayerPropMarket
from app.player_modelling.bookmaker_classification import annotate_price_entries, best_prices, load_bookmaker_info
from app.player_modelling.live_confidence import rare_event_warning
from app.player_modelling.usage_regime import ModelRiskFlag, goal_usage_risk_flags
from app.models.bookmaker import ELIGIBILITY_INCLUDED
from app.player_modelling.live_report_query import (
    EXPECTED_IN_SELECTION_STATUSES,
    current_lineup_for,
    disposal_distribution_for,
    downgrade_confidence,
    goal_distribution_for,
    historical_calibration_metrics,
    price_line,
)
from app.player_modelling.market import PlayerMarket
from app.player_modelling.opportunity_explanation import why_model_likes_it
from app.player_modelling.prop_math import categorize_edge, compare_model_to_market
from app.player_modelling.prop_odds_freshness import DEFAULT_THRESHOLDS, FreshnessThresholds, freshness_state
from app.player_modelling.prop_opportunity_ranking import compute_opportunity_score
from app.player_modelling.request_cache import cached_with_ttl

CONFIDENCE_RANK = {"insufficient_history": 0, "lower_confidence": 1, "moderate_confidence": 2, "higher_confidence": 3}

PRIMARY_SELECTIONS = {"over", "yes", None}  # None: pre-automation manual rows (see PlayerPropMarket.selection docstring)
COMPLEMENTARY_SELECTIONS = {"under", "no"}


@dataclass(frozen=True)
class _MarketKey:
    match_id: int
    player_id: int
    market_type: str
    line_type: str
    threshold: float


def _latest_per_bookmaker(rows: list[PlayerPropMarket]) -> dict[int, PlayerPropMarket]:
    latest: dict[int, PlayerPropMarket] = {}
    for row in rows:
        current = latest.get(row.bookmaker_id)
        if current is None or row.recorded_at > current.recorded_at:
            latest[row.bookmaker_id] = row
    return latest


def load_normalized_prop_insights(
    db: Session,
    *,
    market: str | None = None,
    min_confidence: str | None = None,
    include_uncertain: bool = True,
    opportunities_only: bool = False,
    match_id: int | None = None,
    freshness_thresholds: FreshnessThresholds = DEFAULT_THRESHOLDS,
) -> list[dict]:
    """Short-TTL cross-request cache (see request_cache.py): the Weekly
    Review page builds several hierarchy sections that each call this with
    the same arguments over the same DB state within one request, AND a
    real repeat visit to the page gets a brand-new DB Session each time -
    profiling showed this recomputing the full normalized prop-insights
    pass (~3s) up to 5 times per page load, every load, before this cache
    was added. The underlying data only changes when a live-cycle refresh
    runs, so a 60s TTL trades a small, disclosed staleness window for a
    page that's fast to open repeatedly."""
    key = ("normalized_prop_insights", market, min_confidence, include_uncertain, opportunities_only, match_id, freshness_thresholds)
    return cached_with_ttl(db, key, lambda: _load_normalized_prop_insights_uncached(
        db, market=market, min_confidence=min_confidence, include_uncertain=include_uncertain,
        opportunities_only=opportunities_only, match_id=match_id, freshness_thresholds=freshness_thresholds,
    ))


def _load_normalized_prop_insights_uncached(
    db: Session,
    *,
    market: str | None = None,
    min_confidence: str | None = None,
    include_uncertain: bool = True,
    opportunities_only: bool = False,
    match_id: int | None = None,
    freshness_thresholds: FreshnessThresholds = DEFAULT_THRESHOLDS,
) -> list[dict]:
    stmt = select(PlayerPropMarket)
    if market is not None:
        stmt = stmt.where(PlayerPropMarket.market_type == market)
    if match_id is not None:
        stmt = stmt.where(PlayerPropMarket.match_id == match_id)
    all_rows = db.scalars(stmt).all()
    bookmaker_info = load_bookmaker_info(db)

    grouped: dict[_MarketKey, list[PlayerPropMarket]] = defaultdict(list)
    for row in all_rows:
        key = _MarketKey(row.match_id, row.player_id, row.market_type, row.line_type, row.threshold)
        grouped[key].append(row)

    results: list[dict] = []
    for key, rows in grouped.items():
        primary_rows = [r for r in rows if r.selection in PRIMARY_SELECTIONS]
        complementary_rows = [r for r in rows if r.selection in COMPLEMENTARY_SELECTIONS]
        if not primary_rows:
            continue

        # Model probability - reuse the promoted projection exactly as
        # load_prop_insights does (Section 13: never a second prop model).
        if key.market_type == PlayerMarket.DISPOSALS.value:
            proj = db.scalar(
                select(PlayerDisposalProjection).where(
                    PlayerDisposalProjection.match_id == key.match_id, PlayerDisposalProjection.player_id == key.player_id
                )
            )
            if proj is None:
                continue
            dist = disposal_distribution_for(proj)
            base_confidence_tier = proj.confidence_tier
            base_warnings = list(proj.warnings)
            input_features = proj.input_features
            usage_regime = proj.usage_regime
            model_risk_flags: list[ModelRiskFlag] = []  # disposal's usage-change effect didn't meet the evidence bar for a flag
        elif key.market_type == PlayerMarket.GOALS.value:
            proj = db.scalar(
                select(PlayerGoalProjection).where(
                    PlayerGoalProjection.match_id == key.match_id, PlayerGoalProjection.player_id == key.player_id
                )
            )
            if proj is None:
                continue
            dist = goal_distribution_for(proj)
            base_confidence_tier = proj.confidence_tier
            base_warnings = list(proj.warnings)
            input_features = proj.input_features
            usage_regime = proj.usage_regime
            model_risk_flags = goal_usage_risk_flags(proj.usage_regime)
        else:
            continue

        current_lineup = current_lineup_for(db, key.player_id, key.match_id)
        selection_status = current_lineup.selection_status if current_lineup else "uncertain"
        is_confirmed = current_lineup.is_confirmed if current_lineup else False
        if selection_status == "confirmed_out":
            continue  # Section 20: never shown as active, regardless of any other filter

        is_uncertain_participation = not is_confirmed and selection_status not in EXPECTED_IN_SELECTION_STATUSES
        if is_uncertain_participation and not include_uncertain:
            continue

        confidence_tier = downgrade_confidence(base_confidence_tier) if is_uncertain_participation else base_confidence_tier
        if min_confidence is not None and CONFIDENCE_RANK.get(confidence_tier, 0) < CONFIDENCE_RANK.get(min_confidence, 0):
            continue

        # --- best price across bookmakers (Section 12) ---
        latest_primary = _latest_per_bookmaker(primary_rows)
        latest_complementary = _latest_per_bookmaker(complementary_rows)

        book_entries = []
        for bookmaker_id, row in latest_primary.items():
            state = freshness_state(row.recorded_at, thresholds=freshness_thresholds)
            book_entries.append(
                {
                    "bookmaker_name": row.bookmaker.name,
                    "price_decimal": row.price_decimal,
                    "recorded_at": row.recorded_at,
                    "freshness": state,
                    "source": row.source,
                    "bookmaker_id": bookmaker_id,
                }
            )
        book_entries.sort(key=lambda e: e["price_decimal"], reverse=True)
        book_entries = annotate_price_entries(book_entries, bookmaker_info)

        # Section 4-5, 13: never let an exchange/excluded bookmaker's price
        # silently become "the" headline price. Prefer eligible bookmakers;
        # only fall back to the full (informational-only) set if literally
        # no enabled bookmaker quotes this exact market, and disclose that
        # via a warning below rather than hiding the market.
        eligible = [e for e in book_entries if e["eligibility"] == ELIGIBILITY_INCLUDED]
        eligible_price_available = bool(eligible)
        candidates = eligible if eligible else book_entries

        non_stale = [e for e in candidates if e["freshness"] != "stale"]
        best_entry = (non_stale or candidates)[0] if candidates else None
        if best_entry is None:
            continue
        price_summary = best_prices(book_entries)

        # --- devig (Section 15): only using the SAME bookmaker's paired side ---
        opposite_odds = None
        overround_note = None
        paired_row = latest_complementary.get(best_entry["bookmaker_id"])
        if paired_row is not None:
            opposite_odds = paired_row.price_decimal
        else:
            overround_note = "Only one side of this market was quoted by the best-priced bookmaker — the raw implied probability likely still includes bookmaker margin."

        model_probability = price_line(dist, key.threshold, key.line_type)
        comparison = compare_model_to_market(model_probability, best_entry["price_decimal"], opposite_side_odds=opposite_odds)
        category = categorize_edge(comparison.difference_pp, confidence_tier)

        warnings = list(base_warnings)
        rare_warning = rare_event_warning(key.market_type, key.threshold, model_probability)
        if rare_warning:
            warnings.append(rare_warning)
        if overround_note:
            warnings.append(overround_note)
        if is_uncertain_participation:
            warnings.append("This player's participation is not confirmed — confidence has been downgraded for this insight.")
        if best_entry["freshness"] == "stale":
            warnings.append("The best available price is stale — treat as indicative only, not currently actionable.")
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
        calibration_metrics = historical_calibration_metrics(db, key.market_type, key.threshold)
        calibration_note = None
        if calibration_metrics is not None:
            calibration_note = (
                f"Historical context: this model's {calibration_metrics.evaluated_threshold:g}+ predictions had a calibration "
                f"error (ECE) of {calibration_metrics.ece:.3f} across {calibration_metrics.n:,} evaluation games "
                f"(nearest evaluated threshold to {calibration_metrics.requested_threshold:g})."
            )
            warnings.append(calibration_note)

        # --- movement (Section 22): across every snapshot ever seen for this market, any bookmaker ---
        movement_rows = sorted(primary_rows, key=lambda r: r.recorded_at)
        first_price = movement_rows[0].price_decimal
        highest_price = max(r.price_decimal for r in movement_rows)
        lowest_price = min(r.price_decimal for r in movement_rows)
        last_movement_at = movement_rows[-1].recorded_at

        opportunity = compute_opportunity_score(
            difference_pp=comparison.difference_pp,
            expected_value=comparison.expected_value,
            confidence_tier=confidence_tier,
            is_uncertain_participation=is_uncertain_participation,
            freshness=best_entry["freshness"],
            has_calibration_data=calibration_note is not None,
        )

        if opportunities_only and comparison.difference_pp <= 0:
            continue

        match = rows[0].match
        player = rows[0].player
        results.append(
            {
                "match_id": key.match_id,
                "round_number": match.round.round_number,
                "season_year": match.season.year,
                "player_id": key.player_id,
                "player_name": player.display_name,
                "team_id": current_lineup.team_id if current_lineup else player.current_team_id,
                "market_type": key.market_type,
                "line_type": key.line_type,
                "threshold": key.threshold,
                "model_probability": comparison.model_probability,
                "model_fair_odds": comparison.model_fair_odds,
                "best_price": best_entry["price_decimal"],
                "best_bookmaker": best_entry["bookmaker_name"],
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
                "raw_implied_probability": comparison.raw_implied_probability,
                "devigged_probability": comparison.devigged_probability,
                "overround_removed": comparison.overround_removed,
                "difference_pp": comparison.difference_pp,
                "expected_value": comparison.expected_value,
                "edge_category": category,
                "confidence_tier": confidence_tier,
                "selection_status": selection_status,
                "is_confirmed": is_confirmed,
                "warnings": warnings,
                "usage_regime": usage_regime,
                "model_risk_flags": [{"code": f.code, "description": f.description} for f in model_risk_flags],
                "n_bookmakers": len(book_entries),
                "bookmakers": book_entries,
                "odds_freshness": best_entry["freshness"],
                "price_movement": {
                    "first_price": first_price,
                    "current_price": best_entry["price_decimal"],
                    "highest_price": highest_price,
                    "lowest_price": lowest_price,
                    "last_movement_at": last_movement_at,
                },
                "snapshot_count": len(movement_rows),
                "why_model_likes_it": why_model_likes_it(key.market_type, comparison.difference_pp, input_features),
                "calibration": (
                    {
                        "evaluated_threshold": calibration_metrics.evaluated_threshold,
                        "ece": calibration_metrics.ece,
                        "n": calibration_metrics.n,
                    }
                    if calibration_metrics is not None
                    else None
                ),
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

    results.sort(key=lambda r: r["opportunity_score"], reverse=True)
    return results
