"""Market movement and closing-price derivation from persisted real
PropMarketObservation snapshots (Sections 5-6 of the market-logging stage
brief) — everything here is read from already-frozen rows, nothing is
recomputed against current model state.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, PropMarketObservation


@dataclass(frozen=True)
class MarketMovement:
    player_id: int
    player_name: str
    match_id: int
    bookmaker_id: int
    bookmaker_name: str
    market_type: str
    line_type: str
    threshold: float
    first_odds: float
    latest_odds: float
    highest_odds: float
    lowest_odds: float
    first_difference_pp: float
    latest_difference_pp: float
    first_observed_at: datetime
    latest_observed_at: datetime
    n_observations: int


def compute_market_movement(
    db: Session, *, match_id: int | None = None, player_id: int | None = None
) -> list[MarketMovement]:
    """Section 5: "For each player/market/bookmaker combination" — grouped
    exactly that way (bookmaker included, since two different bookmakers'
    prices for the "same" market are two independent price histories, not
    one)."""
    stmt = select(PropMarketObservation)
    if match_id is not None:
        stmt = stmt.where(PropMarketObservation.match_id == match_id)
    if player_id is not None:
        stmt = stmt.where(PropMarketObservation.player_id == player_id)
    rows = db.scalars(stmt).all()

    groups: dict[tuple, list[PropMarketObservation]] = defaultdict(list)
    for r in rows:
        key = (r.player_id, r.match_id, r.bookmaker_id, r.market_type, r.line_type, r.threshold)
        groups[key].append(r)

    movements: list[MarketMovement] = []
    for (pid, mid, bid, market_type, line_type, threshold), obs in groups.items():
        obs_sorted = sorted(obs, key=lambda o: o.observed_at)
        first, latest = obs_sorted[0], obs_sorted[-1]
        movements.append(
            MarketMovement(
                player_id=pid,
                player_name=first.player.display_name,
                match_id=mid,
                bookmaker_id=bid,
                bookmaker_name=first.bookmaker.name,
                market_type=market_type,
                line_type=line_type,
                threshold=threshold,
                first_odds=first.offered_odds,
                latest_odds=latest.offered_odds,
                highest_odds=max(o.offered_odds for o in obs),
                lowest_odds=min(o.offered_odds for o in obs),
                first_difference_pp=first.difference_pp,
                latest_difference_pp=latest.difference_pp,
                first_observed_at=first.observed_at,
                latest_observed_at=latest.observed_at,
                n_observations=len(obs),
            )
        )
    return movements


def closing_quote_for(
    db: Session, *, match_id: int, player_id: int, bookmaker_id: int, market_type: str, line_type: str, threshold: float
) -> PropMarketObservation | None:
    """Section 6: "Closing quote" = the latest valid bookmaker quote
    observed BEFORE match start — deliberately NOT claimed as the true
    bookmaker closing line (we don't continuously capture every update,
    only whenever refresh-prop-odds runs) — see closing_price_label()."""
    match = db.get(Match, match_id)
    if match is None:
        return None
    return db.scalar(
        select(PropMarketObservation)
        .where(
            PropMarketObservation.match_id == match_id,
            PropMarketObservation.player_id == player_id,
            PropMarketObservation.bookmaker_id == bookmaker_id,
            PropMarketObservation.market_type == market_type,
            PropMarketObservation.line_type == line_type,
            PropMarketObservation.threshold == threshold,
            PropMarketObservation.observed_at < match.scheduled_start,
        )
        .order_by(PropMarketObservation.observed_at.desc())
        .limit(1)
    )


CLOSING_PRICE_LABEL = "latest observed pre-match price"
