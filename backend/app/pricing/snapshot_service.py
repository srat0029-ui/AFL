"""Prospective evaluation snapshotting + settlement for PricingSnapshot
(B2B Pricing Engine, item 5) — the evidence base for whether the engine's
prices contain real predictive information.

Freezing is idempotent and append-only (see PricingSnapshot's unique
constraint on match+market+model_version): re-running
snapshot_current_round_pricing before kickoff simply skips whatever's
already frozen at this exact model_version, never overwrites it, and a
genuinely new model_version always gets its own new rows rather than
touching the old ones.

Settlement reuses the exact same primitives already used to settle
PropMarketObservation (player markets) and PlacedBet/WeeklyShortlistItem
(team markets) — see prop_settlement.py's _actual_stat_value/
_settle_result/compute_team_market_result. No settlement math is
duplicated here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.edges.fair_odds import fair_odds_from_probability
from app.models import Match, MatchStatus, PlayerMatchStat, PricingSnapshot
from app.pricing.market_intelligence import MarketIntelligence, player_market_intelligence, team_market_intelligence
from app.pricing.player_pricing import DEFAULT_DISPOSAL_THRESHOLDS, DEFAULT_GOAL_THRESHOLDS
from app.player_modelling.market import PlayerMarket
from app.player_modelling.prop_settlement import (
    RESULT_LOST,
    RESULT_PUSH,
    RESULT_VOID,
    RESULT_WON,
    _actual_stat_value,
    _settle_result,
    compute_team_market_result,
)

_RESULT_TO_OUTCOME = {RESULT_WON: "won", RESULT_LOST: "lost", RESULT_PUSH: "push"}


def snapshot_price(
    db: Session, *, match_id: int, player_id: int | None, market_family: str, market_type: str, selection: str,
    line_type: str | None, threshold: float | None, line_value: float | None, model_name: str, model_version: str,
    generated_at: datetime, data_cutoff: datetime, lineup_status: str | None, confidence_tier: str,
    model_probability: float, intelligence: MarketIntelligence | None = None,
) -> PricingSnapshot | None:
    existing = db.scalar(
        select(PricingSnapshot.id).where(
            PricingSnapshot.match_id == match_id, PricingSnapshot.market_type == market_type,
            PricingSnapshot.selection == selection, PricingSnapshot.threshold == threshold,
            PricingSnapshot.line_value == line_value, PricingSnapshot.model_version == model_version,
        )
    )
    if existing is not None:
        return None  # already frozen at this model version - never overwritten, never duplicated

    snap = PricingSnapshot(
        match_id=match_id, player_id=player_id, market_family=market_family, market_type=market_type,
        selection=selection, line_type=line_type, threshold=threshold, line_value=line_value,
        model_name=model_name, model_version=model_version, generated_at=generated_at, data_cutoff=data_cutoff,
        lineup_status=lineup_status, confidence_tier=confidence_tier, model_probability=model_probability,
        model_fair_odds=fair_odds_from_probability(model_probability) if model_probability > 0 else float("inf"),
        best_bookmaker_price=intelligence.best_price if intelligence else None,
        best_bookmaker_name=intelligence.best_bookmaker if intelligence else None,
        market_consensus_probability=(intelligence.consensus.consensus_probability if intelligence and intelligence.consensus else None),
        n_bookmakers=intelligence.n_bookmakers if intelligence else None,
    )
    db.add(snap)
    return snap


@dataclass
class SnapshotReport:
    matches_considered: int = 0
    team_snapshots_created: int = 0
    disposal_snapshots_created: int = 0
    goal_snapshots_created: int = 0


def snapshot_round_pricing(db: Session, match_ids: list[int]) -> SnapshotReport:
    """Freezes team h2h (always) and team line/total (only at whichever
    line values a bookmaker currently quotes for this match, since an
    arbitrary/open-ended line has no natural default to freeze) plus
    player disposals/goals at the standard preset threshold set, for every
    given match. Delegates all actual pricing to team_pricing.py/
    player_pricing.py - this only orchestrates + freezes their output."""
    from app.edges.calculator import build_model_context
    from app.pricing.player_pricing import DISPOSAL_MODEL_NAME, GOAL_MODEL_NAME
    from app.pricing.team_pricing import TEAM_MODEL_NAME, TEAM_MODEL_VERSION, latest_completed_match_timestamp, price_team_market
    from app.models import OddsQuote, PlayerDisposalProjection, PlayerGoalProjection

    report = SnapshotReport()
    now = datetime.now(timezone.utc)
    context = build_model_context(db)
    team_data_cutoff = latest_completed_match_timestamp(db) or now

    for match_id in match_ids:
        match = db.get(Match, match_id)
        if match is None or match.status != MatchStatus.SCHEDULED:
            continue
        report.matches_considered += 1

        distinct_lines = sorted({
            q.line_value for q in db.scalars(select(OddsQuote).where(OddsQuote.match_id == match_id, OddsQuote.market_type == "line")).all()
            if q.line_value is not None
        })
        distinct_totals = sorted({
            q.line_value for q in db.scalars(select(OddsQuote).where(OddsQuote.match_id == match_id, OddsQuote.market_type == "total")).all()
            if q.line_value is not None
        })
        price = price_team_market(match, context, now, team_data_cutoff, line_values=distinct_lines, total_lines=distinct_totals)

        h2h_intel = team_market_intelligence(db, match_id, "h2h", match.home_team.name, None, price.home_win_probability)
        if snapshot_price(
            db, match_id=match_id, player_id=None, market_family="team", market_type="h2h", selection=match.home_team.name,
            line_type=None, threshold=None, line_value=None, model_name=TEAM_MODEL_NAME, model_version=TEAM_MODEL_VERSION,
            generated_at=now, data_cutoff=team_data_cutoff, lineup_status=None, confidence_tier=price.confidence_tier,
            model_probability=price.home_win_probability, intelligence=h2h_intel,
        ) is not None:
            report.team_snapshots_created += 1

        for lp in price.lines:
            intel = team_market_intelligence(db, match_id, "line", lp.home_team, lp.line_value, lp.home_probability)
            if snapshot_price(
                db, match_id=match_id, player_id=None, market_family="team", market_type="line", selection=lp.home_team,
                line_type=None, threshold=None, line_value=lp.line_value, model_name=TEAM_MODEL_NAME, model_version=TEAM_MODEL_VERSION,
                generated_at=now, data_cutoff=team_data_cutoff, lineup_status=None, confidence_tier=price.confidence_tier,
                model_probability=lp.home_probability, intelligence=intel,
            ) is not None:
                report.team_snapshots_created += 1

        for tp in price.totals:
            intel = team_market_intelligence(db, match_id, "total", "over", tp.line_value, tp.over_probability)
            if snapshot_price(
                db, match_id=match_id, player_id=None, market_family="team", market_type="total", selection="over",
                line_type=None, threshold=None, line_value=tp.line_value, model_name=TEAM_MODEL_NAME, model_version=TEAM_MODEL_VERSION,
                generated_at=now, data_cutoff=team_data_cutoff, lineup_status=None, confidence_tier=price.confidence_tier,
                model_probability=tp.over_probability, intelligence=intel,
            ) is not None:
                report.team_snapshots_created += 1

        for row in db.scalars(select(PlayerDisposalProjection).where(PlayerDisposalProjection.match_id == match_id)).all():
            from app.pricing.player_pricing import _threshold_price
            from app.player_modelling.live_report_query import disposal_distribution_for

            dist = disposal_distribution_for(row)
            for t in DEFAULT_DISPOSAL_THRESHOLDS:
                tp = _threshold_price(dist, t)
                intel = player_market_intelligence(db, match_id, row.player_id, PlayerMarket.DISPOSALS.value, "over_under", t, tp.probability)
                if snapshot_price(
                    db, match_id=match_id, player_id=row.player_id, market_family="player_disposals",
                    market_type=PlayerMarket.DISPOSALS.value, selection="over", line_type="over_under", threshold=t, line_value=None,
                    model_name=DISPOSAL_MODEL_NAME, model_version=row.model_version, generated_at=row.generated_at,
                    data_cutoff=row.data_cutoff, lineup_status=row.lineup_status_at_generation, confidence_tier=row.confidence_tier,
                    model_probability=tp.probability, intelligence=intel,
                ) is not None:
                    report.disposal_snapshots_created += 1

        for row in db.scalars(select(PlayerGoalProjection).where(PlayerGoalProjection.match_id == match_id)).all():
            from app.pricing.player_pricing import _threshold_price
            from app.player_modelling.live_report_query import goal_distribution_for

            dist = goal_distribution_for(row)
            for t in DEFAULT_GOAL_THRESHOLDS:
                tp = _threshold_price(dist, t)
                intel = player_market_intelligence(db, match_id, row.player_id, PlayerMarket.GOALS.value, "over_under", t, tp.probability)
                if snapshot_price(
                    db, match_id=match_id, player_id=row.player_id, market_family="player_goals",
                    market_type=PlayerMarket.GOALS.value, selection="over", line_type="over_under", threshold=t, line_value=None,
                    model_name=GOAL_MODEL_NAME, model_version=row.model_version, generated_at=row.generated_at,
                    data_cutoff=row.data_cutoff, lineup_status=row.lineup_status_at_generation, confidence_tier=row.confidence_tier,
                    model_probability=tp.probability, intelligence=intel,
                ) is not None:
                    report.goal_snapshots_created += 1

    db.commit()
    return report


@dataclass
class SettlementReport:
    settled: int = 0
    won: int = 0
    lost: int = 0
    pushed: int = 0
    voided: int = 0
    awaiting_data: int = 0


def _settle_one(db: Session, snap: PricingSnapshot, report: SettlementReport) -> None:
    now = datetime.now(timezone.utc)
    if snap.market_family == "team":
        match = db.get(Match, snap.match_id)
        if match is None or match.status != MatchStatus.COMPLETED:
            report.awaiting_data += 1
            return
        result = compute_team_market_result(match, snap.market_type, snap.selection, snap.line_value)
        if result is None:
            report.awaiting_data += 1
            return
        actual, outcome = result
    else:
        match = db.get(Match, snap.match_id)
        if match is None or match.status != MatchStatus.COMPLETED:
            report.awaiting_data += 1
            return
        stat = db.scalar(select(PlayerMatchStat).where(PlayerMatchStat.match_id == snap.match_id, PlayerMatchStat.player_id == snap.player_id))
        if stat is None:
            any_stats = db.scalar(select(PlayerMatchStat.id).where(PlayerMatchStat.match_id == snap.match_id).limit(1))
            if any_stats is None:
                report.awaiting_data += 1
                return
            snap.outcome, snap.settled_at = RESULT_VOID, now
            report.settled += 1
            report.voided += 1
            return
        actual = _actual_stat_value(stat, snap.market_type)
        if actual is None or actual < 0:
            report.awaiting_data += 1
            return
        result_key = _settle_result(actual, snap.threshold, snap.line_type)
        outcome = _RESULT_TO_OUTCOME.get(result_key)
        if outcome is None:
            report.awaiting_data += 1
            return

    snap.actual_stat_value, snap.outcome, snap.settled_at = actual, outcome, now
    report.settled += 1
    if snap.outcome == "won":
        report.won += 1
    elif snap.outcome == "lost":
        report.lost += 1
    elif snap.outcome == "push":
        report.pushed += 1


def settle_pricing_snapshots(db: Session) -> SettlementReport:
    report = SettlementReport()
    pending = db.scalars(select(PricingSnapshot).where(PricingSnapshot.outcome.is_(None))).all()
    for snap in pending:
        _settle_one(db, snap, report)
    db.commit()
    return report
