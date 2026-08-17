"""Settles PropMarketObservation rows against real PlayerMatchStat results
once a match completes (Sections 7-8 of the market-logging stage brief).
Never touches the quote-time/model-time fields frozen at observation
creation — only ever writes actual_stat_value/market_result/settled_at,
and only once per observation (idempotent — see settle_observation's
settled_at guard, and the unsettled-only filter in settle_match_observations).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MatchStatus, PlayerMatchStat, PropMarketObservation
from app.player_modelling.market import LineType, PlayerMarket

RESULT_WON = "won"
RESULT_LOST = "lost"
RESULT_PUSH = "push"
RESULT_VOID = "void"
RESULT_UNRESOLVED = "unresolved"

# Section 15: an actual stat value settlement cannot trust at face value
# (currently just "negative," the one unambiguous impossibility for a count
# stat) is settled as UNRESOLVED but additionally flagged via
# needs_review/review_reason on the observation itself, so it surfaces
# separately from an ordinary loss rather than being silently treated as
# a normal settled result.
_MIN_PLAUSIBLE_STAT_VALUE = 0


def _actual_stat_value(stat: PlayerMatchStat, market_type: str) -> float | None:
    if market_type == PlayerMarket.DISPOSALS.value:
        return stat.disposals
    if market_type == PlayerMarket.GOALS.value:
        return stat.goals
    return None


def _settle_result(actual: float, threshold: float, line_type: str) -> str:
    """Every observation represents the "over" (or "yes") side only (see
    prop_observation.py — only PRIMARY_SELECTIONS get an observation), so
    settlement only ever needs to ask "did the player clear this line,"
    never which literal side was bet."""
    if line_type == LineType.OVER_UNDER.value:
        # "Over 29.5 wins if actual disposals >= 30" - Section 7's own
        # example is just the arithmetic consequence of actual > threshold
        # for a .5 line; expressed as a strict inequality here so it's
        # correct for a WHOLE-NUMBER threshold too, where actual == threshold
        # is a genuine push (can't happen on a .5 line, but this function
        # doesn't assume every real threshold will be .5 forever).
        if actual > threshold:
            return RESULT_WON
        if actual < threshold:
            return RESULT_LOST
        return RESULT_PUSH
    if line_type == LineType.MULTI_PLUS.value:
        # "N+" is already an inclusive lower bound - actual == threshold
        # wins outright, there is no boundary value that produces a push.
        return RESULT_WON if actual >= threshold else RESULT_LOST
    return RESULT_UNRESOLVED


@dataclass
class SettlementReport:
    matches_considered: int = 0
    matches_not_completed: list[int] = field(default_factory=list)
    observations_settled: int = 0
    observations_won: int = 0
    observations_lost: int = 0
    observations_pushed: int = 0
    observations_voided: int = 0
    observations_unresolved: int = 0
    observations_flagged_for_review: int = 0
    already_settled_skipped: int = 0
    # Left pending (settled_at untouched) because the match is complete but
    # player-match stats haven't been ingested for it AT ALL yet (Sections
    # 10/14) - distinct from "settled" and distinct from a genuine DNP void.
    awaiting_player_stats: int = 0


def settle_observation(db: Session, observation: PropMarketObservation, report: SettlementReport) -> None:
    if observation.settled_at is not None:
        report.already_settled_skipped += 1
        return

    stat = db.scalar(
        select(PlayerMatchStat).where(
            PlayerMatchStat.match_id == observation.match_id, PlayerMatchStat.player_id == observation.player_id
        )
    )
    now = datetime.now(timezone.utc)
    if stat is None:
        # Section 14: "If player stats have not arrived yet: keep pending,
        # label 'Awaiting player stats', retry in future cycle. Do not void
        # merely because stats are delayed." Distinguished from a genuine
        # DNP (below) by whether ANY PlayerMatchStat row exists for this
        # match at all — if other players already have rows, ingestion has
        # clearly run for this match and this player's absence really does
        # mean they didn't play; if NO player has a row yet, ingestion
        # simply hasn't happened for this match yet.
        any_stats_for_match = db.scalar(select(PlayerMatchStat.id).where(PlayerMatchStat.match_id == observation.match_id).limit(1))
        if any_stats_for_match is None:
            report.awaiting_player_stats += 1
            return
        # Section 7: "Do not settle if player result data is unavailable"
        # - void is the honest label (the market can't be adjudicated),
        # distinct from "unresolved" (data exists but couldn't be
        # interpreted) and from simply never running settlement at all.
        observation.market_result = RESULT_VOID
        observation.settled_at = now
        report.observations_settled += 1
        report.observations_voided += 1
        return

    actual = _actual_stat_value(stat, observation.market_type)
    if actual is None:
        observation.market_result = RESULT_UNRESOLVED
        observation.settled_at = now
        report.observations_settled += 1
        report.observations_unresolved += 1
        return

    if actual < _MIN_PLAUSIBLE_STAT_VALUE:
        # Section 15: result verification - a negative count stat is
        # impossible, not just an unusual outcome. Flag for manual review
        # rather than settling it as though the number were trustworthy.
        observation.market_result = RESULT_UNRESOLVED
        observation.needs_review = True
        observation.review_reason = f"Implausible actual value {actual!r} for {observation.market_type} (negative count)."
        observation.settled_at = now
        report.observations_settled += 1
        report.observations_unresolved += 1
        report.observations_flagged_for_review += 1
        return

    result = _settle_result(actual, observation.threshold, observation.line_type)
    observation.actual_stat_value = actual
    observation.market_result = result
    observation.settled_at = now
    report.observations_settled += 1
    if result == RESULT_WON:
        report.observations_won += 1
    elif result == RESULT_LOST:
        report.observations_lost += 1
    elif result == RESULT_PUSH:
        report.observations_pushed += 1
    elif result == RESULT_VOID:
        report.observations_voided += 1
    else:
        report.observations_unresolved += 1


def settle_match_observations(db: Session, match_id: int) -> SettlementReport:
    report = SettlementReport()
    match = db.get(Match, match_id)
    report.matches_considered += 1
    if match is None or match.status != MatchStatus.COMPLETED:
        report.matches_not_completed.append(match_id)
        return report

    unsettled = db.scalars(
        select(PropMarketObservation).where(
            PropMarketObservation.match_id == match_id, PropMarketObservation.settled_at.is_(None)
        )
    ).all()
    for obs in unsettled:
        settle_observation(db, obs, report)
    db.commit()
    return report


def settle_all_completed_matches(db: Session) -> SettlementReport:
    """Section 16: 'find completed matches, find unsettled real prop
    observations' - scoped to matches that actually HAVE at least one
    unsettled observation, not every completed match ever played."""
    match_ids = db.scalars(
        select(PropMarketObservation.match_id)
        .join(Match, Match.id == PropMarketObservation.match_id)
        .where(Match.status == MatchStatus.COMPLETED, PropMarketObservation.settled_at.is_(None))
        .distinct()
    ).all()

    combined = SettlementReport()
    for match_id in match_ids:
        report = settle_match_observations(db, match_id)
        combined.matches_considered += report.matches_considered
        combined.matches_not_completed.extend(report.matches_not_completed)
        combined.observations_settled += report.observations_settled
        combined.observations_won += report.observations_won
        combined.observations_lost += report.observations_lost
        combined.observations_pushed += report.observations_pushed
        combined.observations_voided += report.observations_voided
        combined.observations_unresolved += report.observations_unresolved
        combined.observations_flagged_for_review += report.observations_flagged_for_review
        combined.already_settled_skipped += report.already_settled_skipped
        combined.awaiting_player_stats += report.awaiting_player_stats
    return combined
