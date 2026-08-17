"""Real Market Tracking API — Sections 18-19 of the market-logging stage
brief. Reads only; never triggers a paid provider call (Section 17: "Do
not make paid API calls automatically from frontend page loads" — this
router only ever queries already-persisted PropMarketObservation rows).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import MarketMovementRead, QuoteHistoryEntryRead, RealMarketTrackingReportRead
from app.database import get_db
from app.models import PropMarketObservation
from app.player_modelling.prop_market_movement import compute_market_movement
from app.player_modelling.real_market_tracking import load_real_market_tracking_report

router = APIRouter(prefix="/api/afl", tags=["real-market-tracking"])


@router.get("/real-market-tracking", response_model=RealMarketTrackingReportRead)
def get_real_market_tracking(
    match_id: int | None = None,
    market: str | None = Query(default=None, alias="market_type"),
    db: Session = Depends(get_db),
) -> RealMarketTrackingReportRead:
    report = load_real_market_tracking_report(db, match_id=match_id, market_type=market)
    return RealMarketTrackingReportRead(
        label=report.label,
        summary=report.summary.__dict__,
        model_vs_market=report.model_vs_market.__dict__,
        model_calibration=[b.__dict__ for b in report.model_calibration],
        market_calibration=[b.__dict__ for b in report.market_calibration],
        overall_return=report.overall_return.__dict__,
        edge_buckets=[
            {**{k: v for k, v in b.__dict__.items() if k != "returns"}, "returns": b.returns.__dict__} for b in report.edge_buckets
        ],
        confidence_buckets=[
            {**{k: v for k, v in b.__dict__.items() if k != "returns"}, "returns": b.returns.__dict__} for b in report.confidence_buckets
        ],
        lineup_buckets=[
            {**{k: v for k, v in b.__dict__.items() if k != "returns"}, "returns": b.returns.__dict__} for b in report.lineup_buckets
        ],
        timing_buckets=[
            {**{k: v for k, v in b.__dict__.items() if k != "returns"}, "returns": b.returns.__dict__} for b in report.timing_buckets
        ],
        overall_sample_level=report.overall_sample_level,
        coverage=report.coverage.__dict__,
        market_open_timing=[t.__dict__ for t in report.market_open_timing],
    )


@router.get("/real-market-tracking/quote-history", response_model=list[QuoteHistoryEntryRead])
def get_quote_history(
    player_id: int,
    match_id: int,
    bookmaker_id: int,
    market_type: str,
    line_type: str,
    threshold: float,
    db: Session = Depends(get_db),
) -> list[QuoteHistoryEntryRead]:
    """Section 19: the full chronological price/model-probability history
    for one specific normalized market at one bookmaker, so it's easy to
    see whether a price moved toward or away from the model."""
    rows = db.scalars(
        select(PropMarketObservation)
        .where(
            PropMarketObservation.player_id == player_id,
            PropMarketObservation.match_id == match_id,
            PropMarketObservation.bookmaker_id == bookmaker_id,
            PropMarketObservation.market_type == market_type,
            PropMarketObservation.line_type == line_type,
            PropMarketObservation.threshold == threshold,
        )
        .order_by(PropMarketObservation.observed_at)
    ).all()
    return [
        QuoteHistoryEntryRead(
            observed_at=r.observed_at,
            bookmaker_name=r.bookmaker.name,
            offered_odds=r.offered_odds,
            raw_implied_probability=r.raw_implied_probability,
            devigged_probability=r.devigged_probability,
            model_probability=r.model_probability,
            difference_pp=r.difference_pp,
            confidence_tier=r.confidence_tier,
            selection_status_at_observation=r.selection_status_at_observation,
            market_result=r.market_result,
        )
        for r in rows
    ]


@router.get("/real-market-tracking/movement", response_model=list[MarketMovementRead])
def get_market_movement(
    match_id: int | None = None,
    player_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[MarketMovementRead]:
    movements = compute_market_movement(db, match_id=match_id, player_id=player_id)
    return [MarketMovementRead(**m.__dict__) for m in movements]
