"""Placed Bets tracker API - records bets the user actually placed with
real money, kept entirely separate from anything the app merely surfaced.
See app/player_modelling/placed_bets.py's module docstring: this never
feeds model training or ranking, and offers no staking advice.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.schemas import PlacedBetAnalyticsRead, PlacedBetCreate, PlacedBetRead
from app.database import get_db
from app.player_modelling.placed_bet_analytics import compute_placed_bet_analytics
from app.player_modelling.placed_bets import (
    PlacedBetInput,
    create_placed_bet,
    delete_placed_bet,
    get_placed_bet,
    list_placed_bets,
)

router = APIRouter(prefix="/api/placed-bets", tags=["placed-bets"])


@router.get("", response_model=list[PlacedBetRead])
def get_placed_bets(status: str | None = Query(default=None), db: Session = Depends(get_db)) -> list[PlacedBetRead]:
    return [PlacedBetRead.model_validate(b) for b in list_placed_bets(db, status=status)]


@router.post("", response_model=PlacedBetRead, status_code=201)
def post_placed_bet(payload: PlacedBetCreate, db: Session = Depends(get_db)) -> PlacedBetRead:
    bet = create_placed_bet(db, PlacedBetInput(**payload.model_dump()))
    return PlacedBetRead.model_validate(bet)


@router.get("/analytics", response_model=PlacedBetAnalyticsRead)
def get_placed_bet_analytics(db: Session = Depends(get_db)) -> PlacedBetAnalyticsRead:
    # Registered before /{bet_id} so "analytics" is never swallowed as a bet id.
    analytics = compute_placed_bet_analytics(list_placed_bets(db))
    return PlacedBetAnalyticsRead(
        n_total_settled=analytics.n_total_settled, wins=analytics.wins, losses=analytics.losses,
        voids=analytics.voids, hit_rate=analytics.hit_rate, avg_odds_taken=analytics.avg_odds_taken,
        flat_stake_units=analytics.flat_stake_units, flat_stake_roi_pct=analytics.flat_stake_roi_pct,
        exploratory=analytics.exploratory, min_sample_for_labeled=analytics.min_sample_for_labeled,
        by_source_mode=[s.__dict__ for s in analytics.by_source_mode],
        by_market_type=[s.__dict__ for s in analytics.by_market_type],
        by_probability_bucket=[s.__dict__ for s in analytics.by_probability_bucket],
        by_confidence_tier=[s.__dict__ for s in analytics.by_confidence_tier],
    )


@router.get("/{bet_id}", response_model=PlacedBetRead)
def get_one_placed_bet(bet_id: int, db: Session = Depends(get_db)) -> PlacedBetRead:
    bet = get_placed_bet(db, bet_id)
    if bet is None:
        raise HTTPException(status_code=404, detail="Placed bet not found")
    return PlacedBetRead.model_validate(bet)


@router.delete("/{bet_id}", status_code=204)
def delete_one_placed_bet(bet_id: int, db: Session = Depends(get_db)) -> None:
    if not delete_placed_bet(db, bet_id):
        raise HTTPException(status_code=404, detail="Placed bet not found")
