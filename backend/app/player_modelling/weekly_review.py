"""Weekly Review + Decision Support orchestration (Sections 1-2) — the one
module that assembles every context piece this stage adds (model
strength, similar-situation calibration, direction agreement, projection-
vs-line distance, price sensitivity, market movement, consensus/outlier,
evidence/caution) onto a single opportunity dict, and builds the page's
own hierarchy sections (Strongest Player/Team Opportunities, Markets
Waiting on Team Confirmation, Market Coverage). Reuses every existing
ranking/gating module unmodified — this is presentation/aggregation only.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.calculator import ModelContext, ModelsUnavailableError, build_model_context
from app.models import ExpectedLineup, Match
from app.player_modelling.best_opportunities import load_best_opportunities
from app.player_modelling.consensus_and_outliers import consensus_as_dict, consensus_for_opportunity, detect_outlier_bookmaker, outlier_check_as_dict
from app.player_modelling.context_model_conflict import context_conflict_as_dict, detect_context_conflicts
from app.player_modelling.diversified_opportunities import load_diversified_opportunities
from app.player_modelling.elite_disposal_diagnostic import player_bucket_lookup
from app.player_modelling.evidence_summary import build_evidence_summary, evidence_summary_as_dict
from app.player_modelling.final_shortlist import load_final_shortlist
from app.player_modelling.market_movement import market_movement_as_dict, market_movement_for_opportunity
from app.player_modelling.match_context_service import context_item_as_dict, current_context_for_match
from app.player_modelling.model_market_agreement import direction_agreement_as_dict, direction_agreement_for_opportunity
from app.player_modelling.model_market_disagreements import load_model_market_disagreements
from app.player_modelling.model_strength_context import model_strength_as_dict, model_strength_for_opportunity
from app.player_modelling.price_sensitivity import price_sensitivity_as_dict, price_sensitivity_for_opportunity
from app.player_modelling.projection_line_distance import projection_line_distance_as_dict, projection_line_distance_for_opportunity
from app.player_modelling.similar_situation_calibration import calibration_band_as_dict, calibration_band_for_opportunity
from app.player_modelling.uncertainty_flags import high_tog_volatility
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.player_modelling.weekly_summary import bookmaker_coverage_summary, compute_weekly_summary


def _any_confirmed_player_lineups(db: Session, match_ids: list[int]) -> bool:
    if not match_ids:
        return False
    return (
        db.scalar(select(ExpectedLineup.id).where(ExpectedLineup.match_id.in_(match_ids), ExpectedLineup.is_confirmed.is_(True)).limit(1))
        is not None
    )


def enrich_opportunity_for_review(
    db: Session, opportunity: dict, *, model_context: ModelContext | None, any_confirmed_player_lineups: bool, player_buckets: dict[int, str]
) -> dict:
    """The full Weekly Review comparison-table row: every existing
    opportunity field PLUS this stage's new context, all computed once
    per opportunity (never recomputed per display)."""
    model_strength = model_strength_for_opportunity(db, opportunity)
    calibration_band = calibration_band_for_opportunity(db, opportunity)
    direction = direction_agreement_for_opportunity(opportunity)
    distance = projection_line_distance_for_opportunity(db, opportunity, model_context=model_context)
    price_sensitivity = price_sensitivity_for_opportunity(opportunity)
    movement = market_movement_for_opportunity(db, opportunity)
    consensus = consensus_for_opportunity(db, opportunity)
    outlier = detect_outlier_bookmaker(opportunity["bookmakers"])

    tog_volatile = None
    if opportunity["opportunity_type"] == "player":
        match = db.get(Match, opportunity["match_id"])
        if match is not None:
            tog_volatile = high_tog_volatility(db, opportunity["player_id"], match.scheduled_start)

    elite_bucket = player_buckets.get(opportunity["player_id"]) if opportunity.get("player_id") is not None else None
    recent_form = opportunity.get("recent_form")
    form_disagreement = bool(recent_form and recent_form.get("form_disagreement_label"))

    context_conflict = detect_context_conflicts(db, opportunity)
    current_context_items = current_context_for_match(db, opportunity["match_id"])
    relevant_context = [
        c
        for c in current_context_items
        if (opportunity.get("player_id") is not None and c.player_id == opportunity["player_id"])
        or (c.team_id is not None and c.team_id == opportunity.get("team_id"))
    ]

    evidence = build_evidence_summary(
        opportunity,
        any_confirmed_player_lineups=any_confirmed_player_lineups,
        form_disagreement=form_disagreement,
        calibration_band=calibration_band,
        movement=movement,
        consensus=consensus,
        outlier_check=outlier,
        projection_distance=distance,
        elite_disposal_bucket=elite_bucket,
        tog_volatile=tog_volatile,
        context_conflict=context_conflict,
    )

    return {
        **opportunity,
        "model_strength": model_strength_as_dict(model_strength) if model_strength else None,
        "calibration_band": calibration_band_as_dict(calibration_band) if calibration_band else None,
        "direction_agreement": direction_agreement_as_dict(direction),
        "projection_line_distance": projection_line_distance_as_dict(distance) if distance else None,
        "price_sensitivity": price_sensitivity_as_dict(price_sensitivity),
        "market_movement": market_movement_as_dict(movement) if movement else None,
        "consensus": consensus_as_dict(consensus) if consensus else None,
        "outlier_check": outlier_check_as_dict(outlier) if outlier else None,
        "evidence_summary": evidence_summary_as_dict(evidence),
        "current_context": [context_item_as_dict(c) for c in relevant_context],
        "context_conflict": context_conflict_as_dict(context_conflict),
    }


@dataclass(frozen=True)
class WeeklyReviewPage:
    final_shortlist: list[dict]
    strongest_player_opportunities: list[dict]
    strongest_team_opportunities: list[dict]
    model_vs_market_disagreements_count: int
    markets_waiting_on_team_confirmation: list[dict]
    bookmaker_coverage: list[dict]
    weekly_summary: dict
    any_confirmed_player_lineups: bool


def build_weekly_review_page(db: Session, *, shortlist_limit: int = 10, comparison_limit: int = 10) -> WeeklyReviewPage:
    try:
        model_context = build_model_context(db)
    except ModelsUnavailableError:
        model_context = None
    player_buckets = player_bucket_lookup(db)

    upcoming = load_next_upcoming_round(db)
    match_ids = [m.match_id for m in upcoming]
    any_confirmed = _any_confirmed_player_lineups(db, match_ids)

    def enrich_all(opps: list[dict]) -> list[dict]:
        return [
            enrich_opportunity_for_review(db, o, model_context=model_context, any_confirmed_player_lineups=any_confirmed, player_buckets=player_buckets)
            for o in opps
        ]

    shortlist_result = load_final_shortlist(db, limit=shortlist_limit)
    final_shortlist = enrich_all(shortlist_result.opportunities)

    player_diversified = load_diversified_opportunities(db, view="overall", market_scope="player", include_uncertain=True, limit=comparison_limit)
    strongest_player = enrich_all(player_diversified.opportunities)

    team_diversified = load_diversified_opportunities(db, view="overall", market_scope="team", include_uncertain=True, limit=comparison_limit)
    strongest_team = enrich_all(team_diversified.opportunities)

    # Markets Waiting on Team Confirmation: player opportunities that are
    # NOT confirmed yet but otherwise show a real positive model-market
    # difference - i.e. would be worth another look the moment lineups land.
    all_player = load_best_opportunities(db, market_scope="player", include_uncertain=True, include_stale=True, include_insufficient_history=True)
    waiting_on_confirmation = [o for o in all_player if not o.get("is_confirmed") and o["difference_pp"] > 0][:comparison_limit]
    waiting_enriched = enrich_all(waiting_on_confirmation)

    round_number = upcoming[0].round_number if upcoming else None
    all_passing = load_best_opportunities(db, market_scope="all", include_uncertain=False, include_stale=False, include_insufficient_history=False)
    summary = compute_weekly_summary(round_number, all_passing)
    coverage = bookmaker_coverage_summary(db)
    disagreements_count = len(load_model_market_disagreements(db, limit=None))

    return WeeklyReviewPage(
        final_shortlist=final_shortlist,
        strongest_player_opportunities=strongest_player,
        strongest_team_opportunities=strongest_team,
        model_vs_market_disagreements_count=disagreements_count,
        markets_waiting_on_team_confirmation=waiting_enriched,
        bookmaker_coverage=coverage,
        weekly_summary=summary,
        any_confirmed_player_lineups=any_confirmed,
    )
