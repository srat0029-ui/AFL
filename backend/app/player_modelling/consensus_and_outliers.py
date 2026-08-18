"""Consensus bookmaker probability + outlier detection (Weekly Bet Review
+ Decision Support stage, Sections 9-10).

Methodology (always attached, never left implicit — Section 9's own
"clearly label the methodology"):
- Only ELIGIBLE (non-exchange, non-excluded) bookmakers ever contribute —
  exchange quotes are never mixed in, matching the Market Integrity
  stage's existing eligibility rules everywhere else in this app.
- Where the SAME bookmaker quotes the exact opposite side of the exact
  same market (h2h: the other team; total: the opposite over/under at the
  identical line; player props: the opposite over/under at the identical
  threshold), that bookmaker's contribution is DEVIGGED (proportional
  method, the same app.edges.overround.remove_overround already used
  everywhere else in this app) before being folded into the consensus.
  Where only one side is quoted, the bookmaker's RAW IMPLIED probability
  is used instead, and that row is flagged individually.
- "line" (handicap) markets are never devigged here — the opposite side
  carries a mirrored but numerically DIFFERENT line value (e.g.
  Collingwood +24.5 pairs with Carlton -24.5, not a same-line opposite),
  and reliably matching that pairing is out of scope for this stage. Line
  consensus is always raw implied probability, clearly labelled as such.
- Consensus = a simple, UNWEIGHTED mean of each contributing bookmaker's
  own probability — not liquidity-weighted, not Shin's-method-adjusted.
- Spread = the numeric range (max − min) of implied probability across
  contributing bookmakers — a second way to see "one outlier bookmaker"
  vs "the whole market is genuinely split".

Outlier detection (Section 10) is a separate, purely descriptive flag —
it never rejects or excludes the best price, only discloses that it sits
apart from the rest of the market.
"""

import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.overround import implied_probability, remove_overround
from app.models import Bookmaker, Match, OddsQuote, PlayerPropMarket
from app.models.bookmaker import ELIGIBILITY_INCLUDED

OUTLIER_THRESHOLD_PCT = 20.0


@dataclass(frozen=True)
class BookmakerProbability:
    bookmaker_name: str
    price_decimal: float
    probability: float
    overround_removed: bool


@dataclass(frozen=True)
class ConsensusResult:
    consensus_probability: float
    n_bookmakers: int
    n_devigged: int
    spread: float
    methodology: str
    per_bookmaker: list[BookmakerProbability]


def _methodology_label(market_type: str) -> str:
    if market_type == "line":
        return "Raw implied probability only — the opposite side of a handicap market carries a different line value, so reliable same-book devigging isn't implemented for this market type. Simple unweighted mean across eligible sportsbooks. Exchanges excluded."
    return (
        "Same-book devig where that bookmaker quotes the opposite side of the exact same market; raw implied "
        "probability otherwise. Simple unweighted mean across eligible sportsbooks. Exchanges excluded."
    )


def _compute_consensus(entries: list[BookmakerProbability], market_type: str) -> ConsensusResult | None:
    if not entries:
        return None
    probs = [e.probability for e in entries]
    return ConsensusResult(
        consensus_probability=statistics.fmean(probs),
        n_bookmakers=len(entries),
        n_devigged=sum(1 for e in entries if e.overround_removed),
        spread=max(probs) - min(probs),
        methodology=_methodology_label(market_type),
        per_bookmaker=entries,
    )


def team_consensus(db: Session, opportunity: dict) -> ConsensusResult | None:
    market_type = opportunity["market_type"]
    eligible = [b for b in opportunity["bookmakers"] if b.get("eligibility", ELIGIBILITY_INCLUDED) == ELIGIBILITY_INCLUDED]
    if not eligible:
        return None

    opposite_selection: str | None = None
    opposite_line_value = opportunity["line_value"]
    match = None
    if market_type == "h2h":
        match = db.get(Match, opportunity["match_id"])
        if match is not None:
            opposite_selection = match.away_team.name if opportunity["selection"] == match.home_team.name else match.home_team.name
        opposite_line_value = None
    elif market_type == "total":
        opposite_selection = "under" if opportunity["selection"] == "over" else "over"

    entries = []
    for b in eligible:
        opposite_price = None
        if opposite_selection is not None:
            bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == b["bookmaker_name"]))
            if bookmaker is not None:
                opp_quote = db.scalar(
                    select(OddsQuote)
                    .where(
                        OddsQuote.match_id == opportunity["match_id"], OddsQuote.bookmaker_id == bookmaker.id,
                        OddsQuote.market_type == market_type, OddsQuote.selection == opposite_selection, OddsQuote.line_value == opposite_line_value,
                    )
                    .order_by(OddsQuote.recorded_at.desc())
                )
                opposite_price = opp_quote.price_decimal if opp_quote is not None else None
        if opposite_price is not None:
            fair = remove_overround({"this": b["price_decimal"], "other": opposite_price})
            entries.append(BookmakerProbability(b["bookmaker_name"], b["price_decimal"], fair["this"], True))
        else:
            entries.append(BookmakerProbability(b["bookmaker_name"], b["price_decimal"], implied_probability(b["price_decimal"]), False))

    return _compute_consensus(entries, market_type)


def player_consensus(db: Session, opportunity: dict) -> ConsensusResult | None:
    eligible = [b for b in opportunity["bookmakers"] if b.get("eligibility", ELIGIBILITY_INCLUDED) == ELIGIBILITY_INCLUDED]
    if not eligible:
        return None

    opposite_selection_set = ("under", "no")
    entries = []
    for b in eligible:
        bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == b["bookmaker_name"]))
        opposite_price = None
        if bookmaker is not None:
            opp_quote = db.scalar(
                select(PlayerPropMarket)
                .where(
                    PlayerPropMarket.match_id == opportunity["match_id"], PlayerPropMarket.player_id == opportunity["player_id"],
                    PlayerPropMarket.bookmaker_id == bookmaker.id, PlayerPropMarket.market_type == opportunity["market_type"],
                    PlayerPropMarket.line_type == opportunity["line_type"], PlayerPropMarket.threshold == opportunity["threshold"],
                    PlayerPropMarket.selection.in_(opposite_selection_set),
                )
                .order_by(PlayerPropMarket.recorded_at.desc())
            )
            opposite_price = opp_quote.price_decimal if opp_quote is not None else None
        if opposite_price is not None:
            fair = remove_overround({"this": b["price_decimal"], "other": opposite_price})
            entries.append(BookmakerProbability(b["bookmaker_name"], b["price_decimal"], fair["this"], True))
        else:
            entries.append(BookmakerProbability(b["bookmaker_name"], b["price_decimal"], implied_probability(b["price_decimal"]), False))

    return _compute_consensus(entries, opportunity["market_type"])


def consensus_for_opportunity(db: Session, opportunity: dict) -> ConsensusResult | None:
    if opportunity["opportunity_type"] == "team":
        return team_consensus(db, opportunity)
    return player_consensus(db, opportunity)


def consensus_as_dict(c: ConsensusResult) -> dict:
    return {
        "consensus_probability": c.consensus_probability,
        "n_bookmakers": c.n_bookmakers,
        "n_devigged": c.n_devigged,
        "spread": c.spread,
        "methodology": c.methodology,
        "per_bookmaker": [
            {"bookmaker_name": e.bookmaker_name, "price_decimal": e.price_decimal, "probability": e.probability, "overround_removed": e.overround_removed}
            for e in c.per_bookmaker
        ],
    }


@dataclass(frozen=True)
class OutlierCheck:
    is_outlier: bool
    best_price: float
    median_eligible_price: float
    pct_difference: float
    message: str | None


def detect_outlier_bookmaker(bookmakers: list[dict]) -> OutlierCheck | None:
    """Never rejects the best price — purely descriptive price-shopping
    context, per Section 10's own "do not automatically reject it"."""
    eligible = [b for b in bookmakers if b.get("eligibility", ELIGIBILITY_INCLUDED) == ELIGIBILITY_INCLUDED]
    if len(eligible) < 3:
        return None  # need a meaningful "rest of the market" to compare against
    prices = sorted((b["price_decimal"] for b in eligible), reverse=True)
    best, rest = prices[0], prices[1:]
    median_rest = statistics.median(rest)
    pct_diff = (best - median_rest) / median_rest * 100.0
    is_outlier = pct_diff >= OUTLIER_THRESHOLD_PCT
    return OutlierCheck(
        is_outlier=is_outlier, best_price=best, median_eligible_price=median_rest, pct_difference=pct_diff,
        message="Best price is an outlier versus other sportsbooks" if is_outlier else None,
    )


def outlier_check_as_dict(o: OutlierCheck) -> dict:
    return {
        "is_outlier": o.is_outlier,
        "best_price": o.best_price,
        "median_eligible_price": o.median_eligible_price,
        "pct_difference": o.pct_difference,
        "message": o.message,
    }
