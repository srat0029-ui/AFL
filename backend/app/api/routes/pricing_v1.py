"""B2B AFL Pricing API (versioned - /api/v1/pricing/*, /api/v1/market-intelligence/*).

Two clean, separately-callable layers (item 4):
- /api/v1/pricing/* — pure model belief, independent of any bookmaker.
- /api/v1/market-intelligence/* — comparison of a priced market against
  real bookmaker markets, when one exists. Never influences pricing.

Returns probabilities and prices, never betting-style language. No model
training/re-fitting happens in this request path — see
app/pricing/player_pricing.py and app/pricing/round_pricing.py's module
docstrings for exactly what's read vs computed on demand.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.pricing_schemas import (
    CalibrationInfo,
    DisposalPriceRead,
    GoalPriceRead,
    HealthRead,
    IntegrationHealthRead,
    LinePriceRead,
    MarketIntelligenceRead,
    MatchPricingRead,
    ModelHealthEntry,
    ModelHealthRead,
    ModelProvenance,
    ModelRiskFlagRead,
    RoundPricingRead,
    SameGameLegRead,
    SameGameMultiPriceRead,
    SameGameMultiRequest,
    StaleWarningRead,
    TeamMarketPriceRead,
    ThresholdPriceRead,
    TotalPriceRead,
)
from app.database import get_db
from app.edges.calculator import ModelsUnavailableError, build_model_context
from app.models import GoalModelRun, Match, MatchStatus, PlayerDisposalProjection, PlayerGoalProjection, PlayerModelRun, PricingSnapshot
from app.player_modelling.market import PlayerMarket
from app.pricing.integration_health import load_integration_health
from app.pricing.market_intelligence import player_market_intelligence, team_market_intelligence
from app.pricing.player_pricing import DisposalPrice, GoalPrice, price_disposals, price_goals
from app.pricing.round_pricing import RoundPricing, price_current_round
from app.pricing.same_game_pricing import SameGameMultiPrice, SgmLegRequest, SgmValidationError, price_same_game_multi
from app.pricing.team_pricing import TeamMarketPrice, latest_completed_match_timestamp, price_team_market

SGM_MODEL_NAME = "sgm_joint_conditional_mc"

pricing_router = APIRouter(prefix="/api/v1/pricing", tags=["pricing-v1"])
market_intel_router = APIRouter(prefix="/api/v1/market-intelligence", tags=["market-intelligence-v1"])
integration_router = APIRouter(prefix="/api/v1", tags=["integration-v1"])


@integration_router.get("/integration-health", response_model=IntegrationHealthRead)
def get_integration_health(db: Session = Depends(get_db)) -> IntegrationHealthRead:
    h = load_integration_health(db)
    return IntegrationHealthRead(
        status=h.status, generated_at=h.generated_at, last_fixture_refresh=h.last_fixture_refresh,
        last_odds_refresh=h.last_odds_refresh, current_round=h.current_round, current_season_year=h.current_season_year,
        promoted_models=h.promoted_models, stale_warnings=[StaleWarningRead(category=w.category, detail=w.detail) for w in h.stale_warnings],
    )


def _provenance(model_name: str, model_version: str, generated_at: datetime, data_cutoff: datetime) -> ModelProvenance:
    return ModelProvenance(model_name=model_name, model_version=model_version, generated_at=generated_at, data_cutoff=data_cutoff)


def _team_read(p: TeamMarketPrice) -> TeamMarketPriceRead:
    return TeamMarketPriceRead(
        match_id=p.match_id, home_team=p.home_team, away_team=p.away_team,
        provenance=_provenance(p.model_name, p.model_version, p.generated_at, p.data_cutoff),
        home_win_probability=p.home_win_probability, draw_probability=p.draw_probability, away_win_probability=p.away_win_probability,
        home_fair_odds=p.home_fair_odds, draw_fair_odds=p.draw_fair_odds, away_fair_odds=p.away_fair_odds,
        expected_margin=p.expected_margin, expected_total_points=p.expected_total_points,
        home_expected_score=p.home_expected_score, away_expected_score=p.away_expected_score,
        lines=[LinePriceRead(**vars(l)) for l in p.lines], totals=[TotalPriceRead(**vars(t)) for t in p.totals],
    )


def _calibration_read(c) -> CalibrationInfo | None:
    if c is None:
        return None
    return CalibrationInfo(market_type=c.market_type, requested_threshold=c.requested_threshold, evaluated_threshold=c.evaluated_threshold, ece=c.ece, n=c.n)


def _disposal_read(p: DisposalPrice) -> DisposalPriceRead:
    return DisposalPriceRead(
        match_id=p.match_id, player_id=p.player_id, player_name=p.player_name, team_id=p.team_id,
        provenance=_provenance(p.model_name, p.model_version, p.generated_at, p.data_cutoff),
        lineup_status=p.lineup_status, confidence_tier=p.confidence_tier, games_of_history=p.games_of_history,
        expected=p.expected, distribution_method=p.distribution_method, distribution_params=p.distribution_params,
        interval_50=p.interval_50, interval_80=p.interval_80, interval_90=p.interval_90,
        thresholds=[ThresholdPriceRead(**vars(t)) for t in p.thresholds], calibration=_calibration_read(p.calibration),
        warnings=p.warnings, is_stale=p.is_stale, stale_reasons=p.stale_reasons,
        usage_regime=p.usage_regime, usage_change_score=p.usage_change_score,
        model_risk_flags=[ModelRiskFlagRead(code=f.code, description=f.description) for f in p.model_risk_flags],
    )


def _goal_read(p: GoalPrice) -> GoalPriceRead:
    return GoalPriceRead(
        match_id=p.match_id, player_id=p.player_id, player_name=p.player_name, team_id=p.team_id,
        provenance=_provenance(p.model_name, p.model_version, p.generated_at, p.data_cutoff),
        lineup_status=p.lineup_status, confidence_tier=p.confidence_tier, games_of_history=p.games_of_history,
        expected=p.expected, distribution_kind=p.distribution_kind, distribution_params=p.distribution_params,
        scoring_archetype=p.scoring_archetype, thresholds=[ThresholdPriceRead(**vars(t)) for t in p.thresholds],
        calibration=_calibration_read(p.calibration), warnings=p.warnings, is_stale=p.is_stale, stale_reasons=p.stale_reasons,
        usage_regime=p.usage_regime, usage_change_score=p.usage_change_score,
        model_risk_flags=[ModelRiskFlagRead(code=f.code, description=f.description) for f in p.model_risk_flags],
    )


def _round_read(r: RoundPricing) -> RoundPricingRead:
    return RoundPricingRead(
        round_number=r.round_number, season_year=r.season_year, n_matches=r.n_matches,
        teams=[_team_read(t) for t in r.teams], disposals=[_disposal_read(d) for d in r.disposals], goals=[_goal_read(g) for g in r.goals],
    )


@pricing_router.get("/health", response_model=HealthRead)
def get_pricing_health(db: Session = Depends(get_db)) -> HealthRead:
    try:
        db.execute(select(1))
        return HealthRead(status="ok", database="reachable")
    except Exception:  # noqa: BLE001 — never leak an internal stack trace to a consumer
        return HealthRead(status="degraded", database="unreachable")


@pricing_router.get("/model-health", response_model=ModelHealthRead)
def get_model_health(db: Session = Depends(get_db)) -> ModelHealthRead:
    entries: list[ModelHealthEntry] = []
    disposal_run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.is_promoted.is_(True)))
    entries.append(ModelHealthEntry(
        model_name="disposal_nb", is_promoted=disposal_run is not None, run_at=disposal_run.run_at if disposal_run else None,
        detail="promoted model available" if disposal_run else "no promoted disposal model — pricing unavailable for this market",
    ))
    goal_run = db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
    entries.append(ModelHealthEntry(
        model_name="goal_hurdle", is_promoted=goal_run is not None, run_at=goal_run.run_at if goal_run else None,
        detail="promoted model available" if goal_run else "no promoted goal model — pricing unavailable for this market",
    ))
    try:
        build_model_context(db)
        entries.append(ModelHealthEntry(model_name="elo_poisson", is_promoted=True, run_at=None, detail="Elo/Poisson team model context builds successfully"))
    except ModelsUnavailableError as exc:
        entries.append(ModelHealthEntry(model_name="elo_poisson", is_promoted=False, run_at=None, detail=str(exc)))
    return ModelHealthRead(generated_at=datetime.now(timezone.utc), models=entries)


@pricing_router.get("/afl/matches/{match_id}", response_model=MatchPricingRead)
def get_match_pricing(match_id: int, db: Session = Depends(get_db)) -> MatchPricingRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="match not found")
    try:
        context = build_model_context(db)
    except ModelsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    now = datetime.now(timezone.utc)
    data_cutoff = latest_completed_match_timestamp(db) or now
    team = price_team_market(match, context, now, data_cutoff)
    disposals = [price_disposals(db, r) for r in db.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id)).all()]
    goals = [price_goals(db, r) for r in db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id)).all()]

    return MatchPricingRead(match_id=match_id, team=_team_read(team), disposals=[_disposal_read(d) for d in disposals], goals=[_goal_read(g) for g in goals])


@pricing_router.get("/afl/current-round", response_model=RoundPricingRead)
def get_current_round_pricing(no_cache: bool = Query(default=False), db: Session = Depends(get_db)) -> RoundPricingRead:
    try:
        result = price_current_round(db, use_cache=not no_cache)
    except ModelsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return _round_read(result)


@pricing_router.get("/afl/players/{player_id}/disposals", response_model=DisposalPriceRead)
def get_player_disposal_pricing(player_id: int, match_id: int, threshold: list[float] = Query(default=[]), db: Session = Depends(get_db)) -> DisposalPriceRead:
    row = db.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id, PlayerDisposalProjection.player_id == player_id))
    if row is None:
        raise HTTPException(status_code=404, detail="no current disposal projection for this player/match")
    return _disposal_read(price_disposals(db, row, extra_thresholds=threshold or None))


@pricing_router.get("/afl/players/{player_id}/goals", response_model=GoalPriceRead)
def get_player_goal_pricing(player_id: int, match_id: int, threshold: list[float] = Query(default=[]), db: Session = Depends(get_db)) -> GoalPriceRead:
    row = db.scalar(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id, PlayerGoalProjection.player_id == player_id))
    if row is None:
        raise HTTPException(status_code=404, detail="no current goal projection for this player/match")
    return _goal_read(price_goals(db, row, extra_thresholds=threshold or None))


def _same_game_read(p: SameGameMultiPrice) -> SameGameMultiPriceRead:
    return SameGameMultiPriceRead(
        match_id=p.match_id,
        provenance=_provenance(SGM_MODEL_NAME, p.model_version, p.generated_at, p.data_cutoff or p.generated_at),
        model_probability=p.model_probability, model_fair_odds=p.model_fair_odds,
        naive_independence_probability=p.naive_independence_probability, naive_independence_fair_odds=p.naive_independence_fair_odds,
        correlation_adjustment_pp=p.correlation_adjustment_pp, mc_standard_error=p.mc_standard_error,
        n_simulations=p.n_simulations, dependence_validated=p.dependence_validated,
        legs=[SameGameLegRead(leg_type=leg.leg_type, label=leg.label, naive_probability=leg.naive_probability) for leg in p.legs],
    )


@pricing_router.post("/afl/same-game", response_model=SameGameMultiPriceRead)
def post_same_game_multi(request: SameGameMultiRequest, db: Session = Depends(get_db)) -> SameGameMultiPriceRead:
    """Same Game Multi joint pricing — a conditional Monte Carlo model, not
    the naive independence product (see app/pricing/same_game_pricing.py).
    A strongly-correlated leg pairing (e.g. a team's own H2H + that team's
    own line) is rejected with 400, same as the product's Multi Builder."""
    legs = [SgmLegRequest(**leg.model_dump()) for leg in request.legs]
    try:
        price = price_same_game_multi(db, request.match_id, legs, n_simulations=request.n_simulations)
    except SgmValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _same_game_read(price)


# --- Market Intelligence (separate layer) -----------------------------------


def _intel_read(m) -> MarketIntelligenceRead:
    from app.api.pricing_schemas import BookLineRead, ConsensusRead, OutlierRead

    return MarketIntelligenceRead(
        has_market=m.has_market, n_bookmakers=m.n_bookmakers, best_price=m.best_price, best_bookmaker=m.best_bookmaker,
        consensus=ConsensusRead(
            consensus_probability=m.consensus.consensus_probability, n_bookmakers=m.consensus.n_bookmakers,
            n_devigged=m.consensus.n_devigged, spread=m.consensus.spread, methodology=m.consensus.methodology,
        ) if m.consensus else None,
        outlier=OutlierRead(
            is_outlier=m.outlier.is_outlier, best_price=m.outlier.best_price, median_eligible_price=m.outlier.median_eligible_price,
            pct_difference=m.outlier.pct_difference, message=m.outlier.message,
        ) if m.outlier else None,
        model_probability=m.model_probability, market_implied_probability=m.market_implied_probability, difference_pp=m.difference_pp,
        books=[BookLineRead(bookmaker_name=b.bookmaker_name, price_decimal=b.price_decimal, recorded_at=b.recorded_at, eligibility=b.eligibility) for b in m.books],
    )


@market_intel_router.get("/afl/matches/{match_id}/team/{market_type}", response_model=MarketIntelligenceRead)
def get_team_market_intelligence(
    match_id: int, market_type: str, selection: str, line_value: float | None = None, db: Session = Depends(get_db)
) -> MarketIntelligenceRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="match not found")
    try:
        context = build_model_context(db)
    except ModelsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    now = datetime.now(timezone.utc)
    price = price_team_market(match, context, now, latest_completed_match_timestamp(db) or now, line_values=[line_value] if market_type == "line" and line_value is not None else None, total_lines=[line_value] if market_type == "total" and line_value is not None else None)
    model_p = {
        "h2h": price.home_win_probability if selection == match.home_team.name else price.away_win_probability,
    }.get(market_type)
    if model_p is None and price.lines:
        model_p = price.lines[0].home_probability if selection == match.home_team.name else price.lines[0].away_probability
    if model_p is None and price.totals:
        model_p = price.totals[0].over_probability if selection == "over" else price.totals[0].under_probability
    if model_p is None:
        raise HTTPException(status_code=400, detail="unsupported market_type/selection combination")
    return _intel_read(team_market_intelligence(db, match_id, market_type, selection, line_value, model_p))


@market_intel_router.get("/afl/players/{player_id}/{market_type}", response_model=MarketIntelligenceRead)
def get_player_market_intelligence(
    player_id: int, market_type: str, match_id: int, threshold: float, line_type: str = "over_under", db: Session = Depends(get_db)
) -> MarketIntelligenceRead:
    if market_type == PlayerMarket.DISPOSALS.value:
        row = db.scalar(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id, PlayerDisposalProjection.player_id == player_id))
        priced = price_disposals(db, row, extra_thresholds=[threshold]) if row else None
    elif market_type == PlayerMarket.GOALS.value:
        row = db.scalar(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id, PlayerGoalProjection.player_id == player_id))
        priced = price_goals(db, row, extra_thresholds=[threshold]) if row else None
    else:
        raise HTTPException(status_code=400, detail="market_type must be player_disposals or player_goals")
    if row is None:
        raise HTTPException(status_code=404, detail="no current projection for this player/match")

    # extra_thresholds is always appended after the preset set (see
    # player_pricing.py's price_disposals/price_goals), so the requested
    # threshold's price is exactly the last entry.
    model_p = priced.thresholds[-1].probability
    return _intel_read(player_market_intelligence(db, match_id, player_id, market_type, line_type, threshold, model_p))
