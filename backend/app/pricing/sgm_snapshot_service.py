"""Prospective evaluation snapshotting + settlement for Same Game Multi
joint prices — the SGM-specific sibling of app/pricing/snapshot_service.py,
kept as its own module because a joint SGM price is a genuinely different
shape (multi-leg, repeatedly snapshotted across a pre-match horizon) — see
app/models/sgm_price_snapshot.py's module docstring for why this isn't
bolted onto PricingSnapshot.

Never refits anything: `snapshot_sgm_pricing` only reads whatever
Multi Builder's own combo search + SGM enrichment (app/player_modelling/
multi_builder.py's build_match_multis/_try_price_same_game, itself reading
already-fitted disposal/goal projections, live Poisson team context, and
the already-fitted SgmDependenceCoefficient) already computed — nothing
here trains or tunes anything.

Which combos get frozen is deliberately NOT a new policy invented here:
every option Multi Builder's existing, already-tested search actually
surfaces (across both modes, every tier) that qualifies for SGM pricing
gets frozen — the same combos a real user of the product would see, not an
arbitrary new set of "all possible pairings."

Settlement reuses the exact same primitives every other settlement path in
this codebase uses (app/player_modelling/prop_settlement.py) — no
settlement math is duplicated here. Combo-level aggregation across legs
mirrors placed_bets.compute_multi_group_status's precedence (any LOST leg
kills the combo immediately regardless of what else is pending; void/push
legs are removed from consideration; WON if anything is left, VOID if
nothing is) — same logic, re-expressed here because SgmSnapshotLeg's
outcome vocabulary ("won"/"lost"/"push"/"void") differs from PlacedBet's
status constants, not because the underlying rule is different.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market_monitor.common import aware
from app.models import (
    Match,
    MatchStatus,
    PlayerMatchStat,
    SgmPriceSnapshot,
    SgmSnapshotLeg,
    Team,
)
from app.models.sgm_price_snapshot import (
    SNAPSHOT_HORIZON_1H_6H,
    SNAPSHOT_HORIZON_6H_24H,
    SNAPSHOT_HORIZON_24H_PLUS,
    SNAPSHOT_HORIZON_UNDER_1H,
)
from app.player_modelling.market import PlayerMarket
from app.player_modelling.multi_builder import (
    MODE_HIGH_PROBABILITY,
    MODE_VALUE,
    build_match_multis,
    match_multi_tiers_as_dict,
)
from app.player_modelling.prop_settlement import (
    RESULT_LOST,
    RESULT_PUSH,
    RESULT_VOID,
    RESULT_WON,
    _actual_stat_value,
    _settle_result,
    compute_team_market_result,
)

# The player leg thresholds this engine prices are always half-integer
# "clears this line" values (see app/player_modelling/sgm_dependence.py -
# both disposals and goals legs use `samples >= math.ceil(threshold)`),
# which is exactly LineType.OVER_UNDER's settlement semantics
# (`actual > threshold`) for EITHER market - never the "N+" MULTI_PLUS
# convention real bookmaker goal-prop quotes use elsewhere in this
# codebase. Both markets settle through the identical primitive call.
_SGM_LEG_SETTLEMENT_MARKET_TYPE = {"disposals": PlayerMarket.DISPOSALS.value, "goals": PlayerMarket.GOALS.value}
_OVER_UNDER = "over_under"

_RESULT_TO_OUTCOME = {RESULT_WON: "won", RESULT_LOST: "lost", RESULT_PUSH: "push"}


def compute_snapshot_horizon(hours_to_kickoff: float) -> str:
    """The four buckets requested, no others: >=24h, [6h,24h), [1h,6h), <1h."""
    if hours_to_kickoff >= 24:
        return SNAPSHOT_HORIZON_24H_PLUS
    if hours_to_kickoff >= 6:
        return SNAPSHOT_HORIZON_6H_24H
    if hours_to_kickoff >= 1:
        return SNAPSHOT_HORIZON_1H_6H
    return SNAPSHOT_HORIZON_UNDER_1H


def _leg_component(leg: dict) -> tuple[str, str]:
    """Returns (canonical_leg_type, signature_component) for one Multi
    Builder leg dict. canonical_leg_type is this engine's own vocabulary
    ("h2h"/"total"/"disposals"/"goals"), not best_opportunities.py's
    ("h2h"/"total"/"player_disposals"/"player_goals")."""
    if leg["opportunity_type"] == "team":
        leg_type = leg["market_type"]  # already "h2h" | "total" (never "line" - _try_price_same_game excludes it)
        return leg_type, f"{leg_type}:{leg.get('team_id')}:{leg.get('line_value')}"
    leg_type = "disposals" if leg["market_type"] == PlayerMarket.DISPOSALS.value else "goals"
    return leg_type, f"{leg_type}:{leg.get('player_id')}:{leg.get('threshold')}"


def _leg_signature(legs: list[dict]) -> str:
    return "|".join(sorted(_leg_component(leg)[1] for leg in legs))


def _leg_type_combination(legs: list[dict]) -> str:
    return "+".join(sorted({_leg_component(leg)[0] for leg in legs}))


def freeze_sgm_price(db: Session, *, match_id: int, option: dict, generated_at: datetime, hours_to_kickoff: float) -> SgmPriceSnapshot | None:
    """Idempotent create: a pre-check query (mirroring snapshot_price's
    two-layer approach) backed by a real DB UniqueConstraint on
    (match_id, leg_signature, model_version, snapshot_horizon) - calling
    this twice with identical inputs within the same horizon window is a
    guaranteed no-op, never a duplicate or an overwrite."""
    sgm = option.get("same_game_pricing")
    if sgm is None:
        return None

    legs = option["legs"]
    signature = _leg_signature(legs)
    horizon = compute_snapshot_horizon(hours_to_kickoff)
    model_version = sgm["model_version"]

    existing = db.scalar(
        select(SgmPriceSnapshot.id).where(
            SgmPriceSnapshot.match_id == match_id, SgmPriceSnapshot.leg_signature == signature,
            SgmPriceSnapshot.model_version == model_version, SgmPriceSnapshot.snapshot_horizon == horizon,
        )
    )
    if existing is not None:
        return None

    snap = SgmPriceSnapshot(
        match_id=match_id, leg_signature=signature, n_legs=len(legs), leg_type_combination=_leg_type_combination(legs),
        snapshot_horizon=horizon, hours_to_kickoff=hours_to_kickoff, model_name="sgm_joint_conditional_mc", model_version=model_version,
        dependence_coefficients_used=sgm.get("_dependence_coefficients_used", {}),
        generated_at=generated_at, data_cutoff=None,
        model_probability=sgm["model_joint_probability"], naive_independence_probability=sgm["naive_independence_probability"],
        correlation_adjustment_pp=sgm["correlation_adjustment_pp"], model_fair_odds=sgm["model_joint_fair_odds"],
        naive_independence_fair_odds=sgm.get("_naive_independence_fair_odds", float("inf")),
        mc_standard_error=sgm["mc_standard_error"], n_simulations=sgm["n_simulations"],
        dependence_validated=sgm["dependence_validated"],
        # bookmaker_sgm_price/name/implied_probability/model_edge stay None -
        # no odds provider integration in this codebase exposes a genuine
        # bookmaker SGM price (see module docstring's sibling in
        # app/models/sgm_price_snapshot.py).
    )
    db.add(snap)
    db.flush()

    for i, leg in enumerate(legs):
        leg_type, _ = _leg_component(leg)
        if leg["opportunity_type"] == "team":
            selection = leg.get("selection") or (db.get(Team, leg["team_id"]).name if leg.get("team_id") else "")
            db.add(SgmSnapshotLeg(
                snapshot_id=snap.id, leg_index=i, leg_type=leg_type, team_id=leg.get("team_id"), player_id=None,
                selection=selection, threshold=None, line_value=leg.get("line_value"),
                naive_leg_probability=leg["model_probability"],
            ))
        else:
            db.add(SgmSnapshotLeg(
                snapshot_id=snap.id, leg_index=i, leg_type=leg_type, team_id=None, player_id=leg["player_id"],
                selection="over", threshold=leg.get("threshold"), line_value=None,
                naive_leg_probability=leg["model_probability"],
            ))

    return snap


@dataclass
class SgmSnapshotReport:
    matches_considered: int = 0
    snapshots_created: int = 0


def snapshot_sgm_pricing(db: Session, match_ids: list[int]) -> SgmSnapshotReport:
    """For every still-scheduled match, runs Multi Builder's existing combo
    search (both modes, every tier) and freezes every option whose SGM
    enrichment came back non-null. Idempotency (see freeze_sgm_price)
    naturally collapses the same real combo surfaced under multiple
    tiers/modes into one row per horizon window - no separate dedup pass
    needed, matching how snapshot_round_pricing already relies on
    snapshot_price's idempotency rather than pre-filtering."""
    report = SgmSnapshotReport()
    now = datetime.now(timezone.utc)

    for match_id in match_ids:
        match = db.get(Match, match_id)
        if match is None or match.status != MatchStatus.SCHEDULED:
            continue
        report.matches_considered += 1
        hours_to_kickoff = (aware(match.scheduled_start) - now).total_seconds() / 3600.0

        for mode in (MODE_HIGH_PROBABILITY, MODE_VALUE):
            result = build_match_multis(db, match_id, confirmed_only=True, mode=mode)
            result_dict = match_multi_tiers_as_dict(db, result)
            for tier in result_dict["tiers"]:
                for option in tier["options"]:
                    snap = freeze_sgm_price(db, match_id=match_id, option=option, generated_at=now, hours_to_kickoff=hours_to_kickoff)
                    if snap is not None:
                        report.snapshots_created += 1

    db.commit()
    return report


@dataclass
class SgmSettlementReport:
    combos_settled: int = 0
    combos_won: int = 0
    combos_lost: int = 0
    combos_voided: int = 0
    legs_resolved: int = 0
    awaiting_data: int = 0


def _settle_leg(db: Session, leg: SgmSnapshotLeg, match: Match, now: datetime) -> None:
    """Write-once: a leg that already has an outcome is never touched
    again. Leaves leg_outcome=None (unchanged) when the underlying
    match/player data isn't available yet - safe to call every cycle."""
    if leg.leg_outcome is not None:
        return

    if leg.leg_type in ("h2h", "total"):
        if match.status != MatchStatus.COMPLETED:
            return
        result = compute_team_market_result(match, leg.leg_type, leg.selection, leg.line_value)
        if result is None:
            return
        actual, result_key = result
        outcome = _RESULT_TO_OUTCOME.get(result_key)
        if outcome is None:
            return
        leg.actual_value, leg.leg_outcome, leg.leg_resolved_at = actual, outcome, now
        return

    if match.status != MatchStatus.COMPLETED:
        return
    stat = db.scalar(select(PlayerMatchStat).where(PlayerMatchStat.match_id == match.id, PlayerMatchStat.player_id == leg.player_id))
    if stat is None:
        any_stats = db.scalar(select(PlayerMatchStat.id).where(PlayerMatchStat.match_id == match.id).limit(1))
        if any_stats is None:
            return  # stats haven't landed for this match at all yet - retry later
        leg.leg_outcome, leg.leg_resolved_at = RESULT_VOID, now  # genuine DNP
        return
    market_type = _SGM_LEG_SETTLEMENT_MARKET_TYPE[leg.leg_type]
    actual = _actual_stat_value(stat, market_type)
    if actual is None or actual < 0:
        return
    result_key = _settle_result(actual, leg.threshold, _OVER_UNDER)
    outcome = _RESULT_TO_OUTCOME.get(result_key)
    if outcome is None:
        return
    leg.actual_value, leg.leg_outcome, leg.leg_resolved_at = actual, outcome, now


def _combo_status(legs: list[SgmSnapshotLeg]) -> str | None:
    """Same precedence as placed_bets.compute_multi_group_status, adapted
    to SgmSnapshotLeg's outcome vocabulary: any LOST leg decides the combo
    immediately, even with other legs still pending. Returns None (stays
    pending) only when nothing has lost and something is still unresolved."""
    if any(leg.leg_outcome == "lost" for leg in legs):
        return "lost"
    if any(leg.leg_outcome is None for leg in legs):
        return None
    remaining = [leg for leg in legs if leg.leg_outcome not in ("push", "void")]
    if not remaining:
        return "void"
    return "won"


def settle_sgm_snapshots(db: Session) -> SgmSettlementReport:
    report = SgmSettlementReport()
    now = datetime.now(timezone.utc)
    pending = db.scalars(select(SgmPriceSnapshot).where(SgmPriceSnapshot.outcome.is_(None))).all()

    for snap in pending:
        match = db.get(Match, snap.match_id)
        if match is None:
            report.awaiting_data += 1
            continue
        newly_resolved = 0
        for leg in snap.legs:
            was_unresolved = leg.leg_outcome is None
            _settle_leg(db, leg, match, now)
            if was_unresolved and leg.leg_outcome is not None:
                newly_resolved += 1
        report.legs_resolved += newly_resolved

        outcome = _combo_status(snap.legs)
        if outcome is None:
            report.awaiting_data += 1
            continue
        snap.outcome, snap.settled_at = outcome, now
        report.combos_settled += 1
        if outcome == "won":
            report.combos_won += 1
        elif outcome == "lost":
            report.combos_lost += 1
        elif outcome == "void":
            report.combos_voided += 1

    db.commit()
    return report
