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

import logging
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
    Bookmaker,
    Match,
    MatchStatus,
    PlacedBet,
    PlayerMatchStat,
    PlayerPropMarket,
)
from app.player_modelling.prop_settlement import (
    RESULT_LOST,
    RESULT_PUSH,
    RESULT_WON,
    _actual_stat_value,
    _settle_result,
    compute_team_market_result,
)

logger = logging.getLogger(__name__)

_RESULT_TO_STATUS = {RESULT_WON: STATUS_WON, RESULT_LOST: STATUS_LOST, RESULT_PUSH: STATUS_PUSH}

# A leg in one of these statuses is removed from a multi's win/loss
# determination rather than blocking it - the same convention a bookmaker
# applies (a void/push leg reprices the multi with one fewer leg, it
# doesn't unresolve the whole thing).
_MULTI_LEG_REMOVED_STATUSES = {STATUS_VOID, STATUS_PUSH}

# How close a legacy market snapshot's price has to be to the frozen
# odds_taken to count as "the same selection" when recovering a missing
# threshold/line_type (see _repair_missing_line_info) - not exact-float
# equality since these are both floats round-tripped through JSON/DB.
_PRICE_RECOVERY_TOLERANCE = 0.005


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
    model_version: str | None = None
    multi_group_id: str | None = None
    multi_tier: str | None = None
    multi_indicative_odds: float | None = None


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
        model_version=data.model_version, multi_group_id=data.multi_group_id, multi_tier=data.multi_tier,
        multi_indicative_odds=data.multi_indicative_odds,
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
    awaiting_data: int = 0  # kept for backward compatibility; see the two more specific counters below
    # Diagnostics (Section 6 of the settlement reliability audit) - split
    # out from the single awaiting_data bucket above so "why is this still
    # pending" can be answered without opening the DB.
    legs_checked: int = 0
    matches_awaiting_result: int = 0  # team-market leg: match not yet COMPLETED
    matches_awaiting_player_stats: int = 0  # player-market leg: match complete, but no stats ingested for it at all yet
    legs_repaired: int = 0  # legacy leg missing threshold/line_type, recovered from market history
    settlement_failures: int = 0  # data exists but couldn't be turned into a result - needs investigation, not a retry
    multis_won: int = 0
    multis_lost: int = 0
    multis_voided: int = 0
    multis_settled: int = 0  # = multis_won + multis_lost + multis_voided, multi_group_ids newly fully resolved this run


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
    logger.info(
        "placed_bet_settlement.leg_settled bet_id=%s match_id=%s player_id=%s market_type=%s status=%s actual=%s",
        bet.id, bet.match_id, bet.player_id, bet.market_type, status, actual,
    )


def _repair_missing_line_info(db: Session, bet: PlacedBet) -> bool:
    """Best-effort, non-guessing recovery of threshold/line_type for a
    legacy player-market PlacedBet frozen without them (a data-entry gap,
    not an intentional omission - see the module docstring's "freezes
    exactly what was passed in": a bet that never captured these fields at
    all was never validly frozen in the first place). Reconstructed from
    the same PlayerPropMarket snapshot history the opportunity was
    originally shown from, matched on (match, player, market_type,
    bookmaker) with the frozen odds_taken as the disambiguating key, at or
    before the moment the bet was placed. Only ever fills in a NULL field -
    never touches one that already has a value - and only commits to a
    recovery when exactly one (line_type, threshold) pair matches; anything
    ambiguous is left alone and surfaces as a settlement failure instead.
    """
    if bet.opportunity_type != "player":
        return False
    if bet.threshold is not None and bet.line_type is not None:
        return False
    bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == bet.bookmaker))
    if bookmaker is None:
        return False
    candidates = db.scalars(
        select(PlayerPropMarket)
        .where(
            PlayerPropMarket.match_id == bet.match_id,
            PlayerPropMarket.player_id == bet.player_id,
            PlayerPropMarket.market_type == bet.market_type,
            PlayerPropMarket.bookmaker_id == bookmaker.id,
            PlayerPropMarket.recorded_at <= bet.placed_at,
        )
        .order_by(PlayerPropMarket.recorded_at.desc())
    ).all()
    price_matches = [c for c in candidates if abs(c.price_decimal - bet.odds_taken) <= _PRICE_RECOVERY_TOLERANCE]
    if not price_matches:
        return False
    most_recent_at = price_matches[0].recorded_at
    same_moment = [c for c in price_matches if c.recorded_at == most_recent_at]
    distinct_lines = {(c.line_type, c.threshold) for c in same_moment}
    if len(distinct_lines) != 1:
        return False  # genuinely ambiguous at this snapshot - don't guess
    recovered_line_type, recovered_threshold = next(iter(distinct_lines))
    bet.line_type = recovered_line_type
    bet.threshold = recovered_threshold
    logger.warning(
        "placed_bet_settlement.legacy_line_info_repaired bet_id=%s match_id=%s player_id=%s "
        "recovered_line_type=%s recovered_threshold=%s source_market_row_id=%s",
        bet.id, bet.match_id, bet.player_id, recovered_line_type, recovered_threshold, same_moment[0].id,
    )
    return True


def _settle_one(db: Session, bet: PlacedBet, report: PlacedBetSettlementReport) -> None:
    if bet.opportunity_type == "player":
        if bet.threshold is None or bet.line_type is None:
            if _repair_missing_line_info(db, bet):
                report.legs_repaired += 1
            if bet.threshold is None or bet.line_type is None:
                report.settlement_failures += 1
                logger.error(
                    "placed_bet_settlement.settlement_failure bet_id=%s match_id=%s player_id=%s reason=%s",
                    bet.id, bet.match_id, bet.player_id, "missing threshold/line_type, unrecoverable from market history",
                )
                return
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
                report.matches_awaiting_player_stats += 1
                return
            _mark_settled(bet, STATUS_VOID, None, report)
            return
        actual = _actual_stat_value(stat, bet.market_type)
        if actual is None or actual < 0:
            report.settlement_failures += 1
            logger.error(
                "placed_bet_settlement.settlement_failure bet_id=%s match_id=%s player_id=%s reason=%s actual=%s",
                bet.id, bet.match_id, bet.player_id, "implausible or unresolvable actual stat value", actual,
            )
            return
        result = _settle_result(actual, bet.threshold, bet.line_type)
        status = _RESULT_TO_STATUS.get(result)
        if status is None:
            report.settlement_failures += 1
            logger.error(
                "placed_bet_settlement.settlement_failure bet_id=%s match_id=%s player_id=%s reason=%s line_type=%s",
                bet.id, bet.match_id, bet.player_id, "unresolved line_type", bet.line_type,
            )
            return
        _mark_settled(bet, status, actual, report)
    else:
        match = db.get(Match, bet.match_id)
        if match is None or match.status != MatchStatus.COMPLETED:
            report.awaiting_data += 1
            report.matches_awaiting_result += 1
            return
        result = compute_team_market_result(match, bet.market_type, bet.selection, bet.line_value)
        if result is None:
            report.awaiting_data += 1
            report.matches_awaiting_result += 1
            return
        actual, result_key = result
        _mark_settled(bet, _RESULT_TO_STATUS[result_key], actual, report)


def compute_multi_group_status(legs: list[PlacedBet]) -> str:
    """Aggregate win/loss for one multi_group_id from its legs' own
    (independently settled) statuses - the standard multi-betting
    convention: any confirmed LOST leg kills the whole multi outright,
    regardless of what else is still unresolved; a void or push leg is
    removed from consideration rather than blocking anything (mirrors how
    a bookmaker reprices a multi with one fewer leg); once nothing is
    pending and nothing has lost, the multi is WON if any leg is left to
    have won it, or VOID if every leg turned out void/push."""
    if any(leg.status == STATUS_LOST for leg in legs):
        return STATUS_LOST
    if any(leg.status == STATUS_PENDING for leg in legs):
        return STATUS_PENDING
    remaining = [leg for leg in legs if leg.status not in _MULTI_LEG_REMOVED_STATUSES]
    if not remaining:
        return STATUS_VOID
    return STATUS_WON


def settle_placed_bets(db: Session) -> PlacedBetSettlementReport:
    """Idempotent and append-only, same as prop settlement: only ever
    looks at PENDING bets, and each one is settled at most once (its
    status flips away from pending here and is never revisited). Safe to
    call every live cycle (Section 5's catch-up path) - a bet whose result
    only just became available settles the next time this runs; a bet
    still waiting on data is simply looked at again next time, at no cost
    beyond the lookup itself."""
    report = PlacedBetSettlementReport()
    pending = db.scalars(select(PlacedBet).where(PlacedBet.status == STATUS_PENDING)).all()
    report.legs_checked = len(pending)
    touched_group_ids = {bet.multi_group_id for bet in pending if bet.multi_group_id is not None}
    for bet in pending:
        _settle_one(db, bet, report)

    # Multi-level status is derived, never stored - re-read every leg of
    # each multi_group_id touched this run (including ones already settled
    # in a prior run) and see whether the group as a whole just became
    # fully resolved (Section 4). Duplicated legs across different multis
    # (the same player/threshold reused in two tiers) are separate
    # PlacedBet rows that each ran their own settlement above against the
    # same underlying PlayerMatchStat, so they always agree.
    for group_id in touched_group_ids:
        legs = db.scalars(select(PlacedBet).where(PlacedBet.multi_group_id == group_id)).all()
        group_status = compute_multi_group_status(legs)
        if group_status == STATUS_PENDING:
            continue  # still awaiting data on at least one leg - not newly resolved yet
        report.multis_settled += 1
        if group_status == STATUS_WON:
            report.multis_won += 1
        elif group_status == STATUS_LOST:
            report.multis_lost += 1
        elif group_status == STATUS_VOID:
            report.multis_voided += 1
        logger.info("placed_bet_settlement.multi_settled multi_group_id=%s status=%s n_legs=%s", group_id, group_status, len(legs))

    db.commit()
    logger.info(
        "placed_bet_settlement.run_complete legs_checked=%s settled=%s (won=%s lost=%s push=%s void=%s) "
        "repaired=%s awaiting_result=%s awaiting_player_stats=%s failures=%s multis_settled=%s (won=%s lost=%s void=%s)",
        report.legs_checked, report.bets_settled, report.bets_won, report.bets_lost, report.bets_pushed, report.bets_voided,
        report.legs_repaired, report.matches_awaiting_result, report.matches_awaiting_player_stats, report.settlement_failures,
        report.multis_settled, report.multis_won, report.multis_lost, report.multis_voided,
    )
    return report
