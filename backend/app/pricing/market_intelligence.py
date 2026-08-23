"""Market Intelligence — comparison of this engine's pricing against real
bookmaker markets, kept structurally SEPARATE from Pricing (item 4): a
pricing function is never passed a bookmaker price, and nothing here can
feed back into `app.pricing.team_pricing`/`player_pricing`'s output. This
module only ever READS already-recorded OddsQuote/PlayerPropMarket rows
and compares them against a price this engine already computed.

Reuses the existing, already-validated consensus/outlier statistics
(app/player_modelling/consensus_and_outliers.py's same-book-devig
consensus methodology and outlier check) rather than re-implementing
them — this module's own job is just fetching the latest quotes for one
specific market and adapting them into the small dict shape those
functions expect, plus computing the model-market probability difference
and simple price-movement figures for the pricing engine's own price.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bookmaker, OddsQuote, PlayerPropMarket
from app.models.bookmaker import ELIGIBILITY_INCLUDED
from app.player_modelling.consensus_and_outliers import ConsensusResult, OutlierCheck, consensus_for_opportunity, detect_outlier_bookmaker


@dataclass(frozen=True)
class BookLine:
    bookmaker_name: str
    price_decimal: float
    recorded_at: datetime
    eligibility: str


@dataclass(frozen=True)
class MarketIntelligence:
    has_market: bool
    n_bookmakers: int
    best_price: float | None
    best_bookmaker: str | None
    consensus: ConsensusResult | None
    outlier: OutlierCheck | None
    model_probability: float
    market_implied_probability: float | None
    difference_pp: float | None  # model - consensus (or best-price implied, if no consensus), in probability points
    books: list[BookLine] = field(default_factory=list)


def _latest_quotes_per_bookmaker(quotes: list) -> dict[int, object]:
    latest: dict[int, object] = {}
    for q in quotes:
        existing = latest.get(q.bookmaker_id)
        if existing is None or q.recorded_at > existing.recorded_at:
            latest[q.bookmaker_id] = q
    return latest


def team_market_intelligence(db: Session, match_id: int, market_type: str, selection: str, line_value: float | None, model_probability: float) -> MarketIntelligence:
    quotes = db.scalars(
        select(OddsQuote).where(
            OddsQuote.match_id == match_id, OddsQuote.market_type == market_type,
            OddsQuote.selection == selection, OddsQuote.line_value == line_value,
        )
    ).all()
    return _build(db, quotes, model_probability, opportunity_type="team", match_id=match_id, market_type=market_type, selection=selection, line_value=line_value)


def player_market_intelligence(
    db: Session, match_id: int, player_id: int, market_type: str, line_type: str, threshold: float, model_probability: float
) -> MarketIntelligence:
    quotes = db.scalars(
        select(PlayerPropMarket).where(
            PlayerPropMarket.match_id == match_id, PlayerPropMarket.player_id == player_id,
            PlayerPropMarket.market_type == market_type, PlayerPropMarket.line_type == line_type,
            PlayerPropMarket.threshold == threshold, PlayerPropMarket.selection.in_((None, "over")),
        )
    ).all()
    return _build(
        db, quotes, model_probability, opportunity_type="player", match_id=match_id, market_type=market_type,
        selection="over", line_value=None, player_id=player_id, line_type=line_type, threshold=threshold,
    )


def _build(db: Session, quotes: list, model_probability: float, **opportunity_fields) -> MarketIntelligence:
    latest = _latest_quotes_per_bookmaker(quotes)
    if not latest:
        return MarketIntelligence(
            has_market=False, n_bookmakers=0, best_price=None, best_bookmaker=None, consensus=None, outlier=None,
            model_probability=model_probability, market_implied_probability=None, difference_pp=None, books=[],
        )

    bookmaker_rows = {b.id: b for b in db.scalars(select(Bookmaker).where(Bookmaker.id.in_(latest.keys()))).all()}
    books = [
        BookLine(bookmaker_name=bookmaker_rows[bid].name, price_decimal=q.price_decimal, recorded_at=q.recorded_at, eligibility=bookmaker_rows[bid].eligibility)
        for bid, q in latest.items() if bid in bookmaker_rows
    ]
    eligible = [b for b in books if b.eligibility == ELIGIBILITY_INCLUDED]
    best = max(eligible, key=lambda b: b.price_decimal, default=None)

    opportunity = {
        **opportunity_fields,
        "bookmakers": [{"bookmaker_name": b.bookmaker_name, "price_decimal": b.price_decimal, "eligibility": b.eligibility} for b in books],
    }
    consensus = consensus_for_opportunity(db, opportunity)
    outlier = detect_outlier_bookmaker(opportunity["bookmakers"])

    market_prob = consensus.consensus_probability if consensus is not None else (1.0 / best.price_decimal if best else None)
    diff_pp = (model_probability - market_prob) if market_prob is not None else None

    return MarketIntelligence(
        has_market=True, n_bookmakers=len(eligible), best_price=best.price_decimal if best else None,
        best_bookmaker=best.bookmaker_name if best else None, consensus=consensus, outlier=outlier,
        model_probability=model_probability, market_implied_probability=market_prob, difference_pp=diff_pp, books=books,
    )
