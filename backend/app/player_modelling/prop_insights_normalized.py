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
from app.player_modelling.live_confidence import rare_event_warning
from app.player_modelling.live_report_query import (
    EXPECTED_IN_SELECTION_STATUSES,
    current_lineup_for,
    disposal_distribution_for,
    downgrade_confidence,
    goal_distribution_for,
    historical_calibration_note,
    price_line,
)
from app.player_modelling.market import PlayerMarket
from app.player_modelling.prop_math import categorize_edge, compare_model_to_market
from app.player_modelling.prop_odds_freshness import DEFAULT_THRESHOLDS, FreshnessThresholds, freshness_state
from app.player_modelling.prop_opportunity_ranking import compute_opportunity_score

CONFIDENCE_RANK = {"insufficient_history": 0, "lower_confidence": 1, "moderate_confidence": 2, "higher_confidence": 3}

_PRIMARY_SELECTIONS = {"over", "yes", None}  # None: pre-automation manual rows (see PlayerPropMarket.selection docstring)
_COMPLEMENTARY_SELECTIONS = {"under", "no"}


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
    stmt = select(PlayerPropMarket)
    if market is not None:
        stmt = stmt.where(PlayerPropMarket.market_type == market)
    if match_id is not None:
        stmt = stmt.where(PlayerPropMarket.match_id == match_id)
    all_rows = db.scalars(stmt).all()

    grouped: dict[_MarketKey, list[PlayerPropMarket]] = defaultdict(list)
    for row in all_rows:
        key = _MarketKey(row.match_id, row.player_id, row.market_type, row.line_type, row.threshold)
        grouped[key].append(row)

    results: list[dict] = []
    for key, rows in grouped.items():
        primary_rows = [r for r in rows if r.selection in _PRIMARY_SELECTIONS]
        complementary_rows = [r for r in rows if r.selection in _COMPLEMENTARY_SELECTIONS]
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

        non_stale = [e for e in book_entries if e["freshness"] != "stale"]
        best_entry = (non_stale or book_entries)[0] if book_entries else None
        if best_entry is None:
            continue

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
        calibration_note = historical_calibration_note(db, key.market_type, key.threshold)
        if calibration_note:
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
                "market_type": key.market_type,
                "line_type": key.line_type,
                "threshold": key.threshold,
                "model_probability": comparison.model_probability,
                "model_fair_odds": comparison.model_fair_odds,
                "best_price": best_entry["price_decimal"],
                "best_bookmaker": best_entry["bookmaker_name"],
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
