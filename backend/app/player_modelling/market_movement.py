"""Market movement interpretation (Weekly Bet Review + Decision Support
stage, Section 8) — descriptive only, never predictive: first observed
price, latest price, best currently available price, and whether the
market has moved toward or away from the model's own fair price.

"Toward"/"away" is judged in IMPLIED-PROBABILITY space (via
app/edges/overround.implied_probability), not raw decimal odds — decimal
odds are non-linear in probability, so comparing raw price gaps would
mischaracterise movement for long-priced selections. This is purely
descriptive language ("the market has shortened toward the model"/"moved
away from the model") — never a signal about whether the market or the
model is right.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.overround import implied_probability
from app.models import OddsQuote, PlayerPropMarket
from app.models.bookmaker import ELIGIBILITY_INCLUDED

TOWARD_MODEL = "toward_model"
AWAY_FROM_MODEL = "away_from_model"
UNCHANGED = "unchanged"


@dataclass(frozen=True)
class MarketMovement:
    first_price: float
    first_observed_at: datetime
    latest_price: float
    latest_observed_at: datetime
    best_current_price: float
    model_fair_odds: float
    direction: str
    description: str


def _classify_direction(first_price: float, latest_price: float, model_fair_odds: float) -> tuple[str, str]:
    if first_price == latest_price:
        return UNCHANGED, "Price has not moved since it was first observed."

    fair_prob = implied_probability(model_fair_odds)
    first_gap = abs(implied_probability(first_price) - fair_prob)
    latest_gap = abs(implied_probability(latest_price) - fair_prob)

    moved_shorter = latest_price < first_price
    verb = "shortened" if moved_shorter else "drifted"

    if latest_gap < first_gap:
        return (
            TOWARD_MODEL,
            f"Opened ${first_price:.2f}, now ${latest_price:.2f}, model fair ${model_fair_odds:.2f} — market has {verb} toward the model.",
        )
    if latest_gap > first_gap:
        return (
            AWAY_FROM_MODEL,
            f"Opened ${first_price:.2f}, now ${latest_price:.2f}, model fair ${model_fair_odds:.2f} — market has {verb} away from the model.",
        )
    return UNCHANGED, f"Opened ${first_price:.2f}, now ${latest_price:.2f}, model fair ${model_fair_odds:.2f} — equidistant from the model both times."


def team_market_movement(
    db: Session, *, match_id: int, market_type: str, selection: str, line_value: float | None, model_fair_odds: float, best_current_price: float
) -> MarketMovement | None:
    """Across every historical snapshot for this EXACT market (any
    bookmaker) - team OddsQuote rows are append-only, so this reflects the
    market's full observed history, not just the currently-active quotes."""
    quotes = db.scalars(
        select(OddsQuote).where(
            OddsQuote.match_id == match_id, OddsQuote.market_type == market_type, OddsQuote.selection == selection, OddsQuote.line_value == line_value
        )
    ).all()
    if not quotes:
        return None
    quotes_sorted = sorted(quotes, key=lambda q: q.recorded_at)
    first, latest = quotes_sorted[0], quotes_sorted[-1]
    direction, description = _classify_direction(first.price_decimal, latest.price_decimal, model_fair_odds)
    return MarketMovement(
        first_price=first.price_decimal, first_observed_at=first.recorded_at,
        latest_price=latest.price_decimal, latest_observed_at=latest.recorded_at,
        best_current_price=best_current_price, model_fair_odds=model_fair_odds,
        direction=direction, description=description,
    )


def player_market_movement(
    db: Session, *, match_id: int, player_id: int, market_type: str, line_type: str, threshold: float, model_fair_odds: float, best_current_price: float
) -> MarketMovement | None:
    quotes = db.scalars(
        select(PlayerPropMarket).where(
            PlayerPropMarket.match_id == match_id, PlayerPropMarket.player_id == player_id, PlayerPropMarket.market_type == market_type,
            PlayerPropMarket.line_type == line_type, PlayerPropMarket.threshold == threshold, PlayerPropMarket.selection.in_(["over", "yes", None]),
        )
    ).all()
    if not quotes:
        return None
    quotes_sorted = sorted(quotes, key=lambda q: q.recorded_at)
    first, latest = quotes_sorted[0], quotes_sorted[-1]
    direction, description = _classify_direction(first.price_decimal, latest.price_decimal, model_fair_odds)
    return MarketMovement(
        first_price=first.price_decimal, first_observed_at=first.recorded_at,
        latest_price=latest.price_decimal, latest_observed_at=latest.recorded_at,
        best_current_price=best_current_price, model_fair_odds=model_fair_odds,
        direction=direction, description=description,
    )


def market_movement_for_opportunity(db: Session, opportunity: dict) -> MarketMovement | None:
    if opportunity["opportunity_type"] == "team":
        return team_market_movement(
            db, match_id=opportunity["match_id"], market_type=opportunity["market_type"], selection=opportunity["selection"],
            line_value=opportunity["line_value"], model_fair_odds=opportunity["model_fair_odds"], best_current_price=opportunity["best_price"],
        )
    return player_market_movement(
        db, match_id=opportunity["match_id"], player_id=opportunity["player_id"], market_type=opportunity["market_type"],
        line_type=opportunity["line_type"], threshold=opportunity["threshold"], model_fair_odds=opportunity["model_fair_odds"],
        best_current_price=opportunity["best_price"],
    )


def market_movement_as_dict(m: MarketMovement) -> dict:
    return {
        "first_price": m.first_price,
        "first_observed_at": m.first_observed_at,
        "latest_price": m.latest_price,
        "latest_observed_at": m.latest_observed_at,
        "best_current_price": m.best_current_price,
        "model_fair_odds": m.model_fair_odds,
        "direction": m.direction,
        "description": m.description,
    }
