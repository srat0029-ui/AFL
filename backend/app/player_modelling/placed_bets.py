"""Placed-bet tracker: records bets the user actually placed with real
money, kept entirely separate from everything the app merely surfaced
(opportunities, multi legs, shortlist items). Nothing here feeds model
training or ranking - this is personal record-keeping only.

Settlement reuses the exact same primitives already used to settle
PropMarketObservation (player markets - _actual_stat_value/_settle_result)
and WeeklyShortlistSnapshotItem (team markets - compute_team_market_result,
see prop_settlement.py). No settlement math is duplicated here; a
result for a given match+selection is computed the same way everywhere.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    STATUS_LOST,
    STATUS_PENDING,
    STATUS_PUSH,
    STATUS_VOID,
    STATUS_WON,
    Match,
    MatchStatus,
    PlacedBet,
    PlayerMatchStat,
)
from app.player_modelling.prop_settlement import (
    RESULT_LOST,
    RESULT_PUSH,
    RESULT_WON,
    _actual_stat_value,
    _settle_result,
    compute_team_market_result,
)

_RESULT_TO_STATUS = {RESULT_WON: STATUS_WON, RESULT_LOST: STATUS_LOST, RESULT_PUSH: STATUS_PUSH}


@dataclass
class PlacedBetInput:
    match_id: int
    opportunity_type: str  # "player" | "team"
    label: str
    selection: str
    market_type: str
    bookmaker: str
    odds_taken: float
    model_probability: float
    model_fair_odds: float
    confidence_tier: str
    source_mode: str
    player_id: int | None = None
    line_type: str | None = None
    threshold: float | None = None
    line_value: float | None = None
    stake: float | None = None
    lineup_status: str | None = None
    notes: str | None = None
    placed_at: datetime | None = None


def create_placed_bet(db: Session, data: PlacedBetInput) -> PlacedBet:
    """Freezes exactly what was passed in - the caller (API layer, see
    app/api/routes/placed_bets.py) is responsible for sourcing
    model_probability/model_fair_odds/confidence_tier/lineup_status from
    the actual opportunity/multi-leg snapshot at the moment of the click,
    never recomputed here."""
    bet = PlacedBet(
        match_id=data.match_id, player_id=data.player_id, opportunity_type=data.opportunity_type,
        label=data.label, selection=data.selection, market_type=data.market_type,
        line_type=data.line_type, threshold=data.threshold, line_value=data.line_value,
        bookmaker=data.bookmaker, odds_taken=data.odds_taken, stake=data.stake,
        placed_at=data.placed_at or datetime.now(timezone.utc), source_mode=data.source_mode,
        model_probability=data.model_probability, model_fair_odds=data.model_fair_odds,
        confidence_tier=data.confidence_tier, lineup_status=data.lineup_status, notes=data.notes,
        status=STATUS_PENDING,
    )
    db.add(bet)
    db.commit()
    db.refresh(bet)
    return bet


def list_placed_bets(db: Session, status: str | None = None) -> list[PlacedBet]:
    stmt = select(PlacedBet).order_by(PlacedBet.placed_at.desc())
    if status is not None:
        stmt = stmt.where(PlacedBet.status == status)
    return list(db.scalars(stmt).all())


def get_placed_bet(db: Session, bet_id: int) -> PlacedBet | None:
    return db.get(PlacedBet, bet_id)


def delete_placed_bet(db: Session, bet_id: int) -> bool:
    bet = db.get(PlacedBet, bet_id)
    if bet is None:
        return False
    db.delete(bet)
    db.commit()
    return True


@dataclass
class PlacedBetSettlementReport:
    bets_settled: int = 0
    bets_won: int = 0
    bets_lost: int = 0
    bets_pushed: int = 0
    bets_voided: int = 0
    awaiting_data: int = 0


def _mark_settled(bet: PlacedBet, status: str, actual: float | None, report: PlacedBetSettlementReport) -> None:
    bet.actual_stat_value = actual
    bet.status = status
    bet.settled_at = datetime.now(timezone.utc)
    report.bets_settled += 1
    if status == STATUS_WON:
        report.bets_won += 1
    elif status == STATUS_LOST:
        report.bets_lost += 1
    elif status == STATUS_PUSH:
        report.bets_pushed += 1
    elif status == STATUS_VOID:
        report.bets_voided += 1


def _settle_one(db: Session, bet: PlacedBet, report: PlacedBetSettlementReport) -> None:
    if bet.opportunity_type == "player":
        stat = db.scalar(
            select(PlayerMatchStat).where(PlayerMatchStat.match_id == bet.match_id, PlayerMatchStat.player_id == bet.player_id)
        )
        if stat is None:
            # Same distinction prop_settlement.settle_observation makes:
            # no stats for this match AT ALL yet -> keep pending, retry
            # later. Other players already have rows -> a genuine DNP void.
            any_stats = db.scalar(select(PlayerMatchStat.id).where(PlayerMatchStat.match_id == bet.match_id).limit(1))
            if any_stats is None:
                report.awaiting_data += 1
                return
            _mark_settled(bet, STATUS_VOID, None, report)
            return
        actual = _actual_stat_value(stat, bet.market_type)
        if actual is None or actual < 0:
            return  # can't determine a trustworthy result - leave pending rather than guess
        result = _settle_result(actual, bet.threshold, bet.line_type)
        status = _RESULT_TO_STATUS.get(result)
        if status is None:
            return  # unresolved line_type - leave pending, don't guess
        _mark_settled(bet, status, actual, report)
    else:
        match = db.get(Match, bet.match_id)
        if match is None or match.status != MatchStatus.COMPLETED:
            report.awaiting_data += 1
            return
        result = compute_team_market_result(match, bet.market_type, bet.selection, bet.line_value)
        if result is None:
            report.awaiting_data += 1
            return
        actual, result_key = result
        _mark_settled(bet, _RESULT_TO_STATUS[result_key], actual, report)


def settle_placed_bets(db: Session) -> PlacedBetSettlementReport:
    """Idempotent and append-only, same as prop settlement: only ever
    looks at PENDING bets, and each one is settled at most once (its
    status flips away from pending here and is never revisited)."""
    report = PlacedBetSettlementReport()
    pending = db.scalars(select(PlacedBet).where(PlacedBet.status == STATUS_PENDING)).all()
    for bet in pending:
        _settle_one(db, bet, report)
    db.commit()
    return report
