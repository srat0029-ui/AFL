"""Deep-audit assembler (item 5): pulls together everything a human QA
reviewer would want for one case into a single read-only report, reusing
existing, already-validated functions end to end — no new computation.
Also the direct input to root_cause.diagnose (item 4), since a root-cause
finding is really just one section of the same audit.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AnomalyCaseFollowUp, AnomalyCaseSnapshot, PlayerDisposalProjection, PlayerGoalProjection
from app.player_modelling.market import PlayerMarket
from app.pricing.market_intelligence import player_market_intelligence
from app.pricing.player_pricing import price_disposals, price_goals

from app.market_monitor.case_builder import AnomalyCase
from app.market_monitor.common import dedupe_bookmaker_prices
from app.market_monitor.curve_integrity import CurvePoint, check_monotonicity, find_adjacent_jumps
from app.market_monitor.detector import price_single_match
from app.market_monitor.root_cause import RootCauseFinding, diagnose


@dataclass(frozen=True)
class NeighbouringThreshold:
    threshold: float
    model_probability: float
    market_probability: float | None


@dataclass(frozen=True)
class CaseAuditReport:
    case_id: str
    player_name: str | None
    match_id: int
    market_type: str
    threshold: float | None

    current_projection_expected: float | None
    recent_form: dict  # last3/5/10/season/career averages, straight from the persisted projection's input_features
    usage_regime: str | None
    usage_change_score: float | None
    lineup_status: str | None

    bookmaker_prices: list[dict]  # {bookmaker_name, price_decimal, recorded_at, eligibility}
    consensus_methodology: str | None
    n_bookmakers: int
    freshness: str | None

    neighbouring_thresholds: list[NeighbouringThreshold]
    curve_is_monotonic: bool
    curve_has_jumps: bool

    root_cause: RootCauseFinding
    research_category: str | None
    notes: list[str] = field(default_factory=list)


_DISPOSAL_FORM_KEYS = ("disposals_last3_avg", "disposals_last5_avg", "disposals_last10_avg", "disposals_season_avg", "disposals_career_avg")
_GOAL_FORM_KEYS = ("goals_last3_avg", "goals_last5_avg", "goals_last10_avg", "goals_season_avg", "goals_career_avg")


def audit_case(db: Session, case: AnomalyCase, *, n_snapshots: int = 1) -> CaseAuditReport:
    notes: list[str] = []
    _, disposals, goals = price_single_match(db, case.match_id)
    is_disposal = case.market_type == PlayerMarket.DISPOSALS.value
    price = next((p for p in (disposals if is_disposal else goals) if p.player_id == case.player_id), None)

    recent_form: dict = {}
    usage_regime = usage_change_score = lineup_status = current_expected = None
    neighbours: list[NeighbouringThreshold] = []
    curve_monotonic, curve_jumps = True, False

    if price is None:
        notes.append("No current persisted projection for this player/match — the case's evidence reflects the state at detection time, not necessarily right now.")
    else:
        current_expected = price.expected
        usage_regime, usage_change_score, lineup_status = price.usage_regime, price.usage_change_score, price.lineup_status
        # input_features lives on the raw persisted projection row, not on
        # the computed DisposalPrice/GoalPrice pricing output - fetched
        # separately rather than duplicated onto the pricing dataclass.
        form_keys = _DISPOSAL_FORM_KEYS if is_disposal else _GOAL_FORM_KEYS
        model = PlayerDisposalProjection if is_disposal else PlayerGoalProjection
        row = db.scalar(select(model).where(model.match_id == case.match_id, model.player_id == case.player_id))
        recent_form = {k: v for k, v in (row.input_features or {}).items() if k in form_keys} if row is not None else {}

        model_points = [CurvePoint(t.threshold, t.probability) for t in price.thresholds]
        curve_monotonic = check_monotonicity(model_points).is_monotonic
        curve_jumps = len(find_adjacent_jumps(model_points)) > 0

        for t in sorted(price.thresholds, key=lambda x: x.threshold):
            intel = player_market_intelligence(db, case.match_id, case.player_id, case.market_type, t.line_type, t.threshold, t.probability)
            neighbours.append(NeighbouringThreshold(threshold=t.threshold, model_probability=t.probability, market_probability=intel.market_implied_probability))

    primary_intel = player_market_intelligence(db, case.match_id, case.player_id, case.market_type, "over_under", case.threshold, case.primary_alert.model_probability or 0.0) if case.player_id else None

    # Item 7: potential_model_limitation may only escalate from repeated
    # GENUINE PROSPECTIVE evidence, never from a single case or from a
    # retrospective backfill. Once a case has a frozen snapshot, the
    # effective n_snapshots is self-derived from real follow-up history
    # (1 = the freeze itself, +1 per distinct time-to-kickoff stage
    # actually captured by a live-cycle run) - the caller-supplied
    # n_snapshots is only used before a snapshot exists yet.
    snap = db.scalar(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.case_id == case.case_id))
    research_category = None
    effective_n_snapshots = n_snapshots
    if snap is not None:
        research_category = snap.research_category
        if snap.capture_mode == "prospective":
            n_followups = db.scalar(select(func.count()).select_from(AnomalyCaseFollowUp).where(AnomalyCaseFollowUp.snapshot_id == snap.id)) or 0
            effective_n_snapshots = 1 + n_followups
        else:
            effective_n_snapshots = 1  # retrospective evidence never escalates

    root_cause = diagnose(case, n_snapshots=effective_n_snapshots)

    return CaseAuditReport(
        case_id=case.case_id, player_name=case.player_name, match_id=case.match_id, market_type=case.market_type, threshold=case.threshold,
        current_projection_expected=current_expected, recent_form=recent_form, usage_regime=usage_regime, usage_change_score=usage_change_score,
        lineup_status=lineup_status,
        bookmaker_prices=[{"bookmaker_name": b.bookmaker_name, "price_decimal": b.price_decimal, "recorded_at": b.recorded_at.isoformat(), "eligibility": b.eligibility} for b in dedupe_bookmaker_prices([b for a in case.alerts for b in a.bookmaker_prices])],
        consensus_methodology=primary_intel.consensus.methodology if primary_intel and primary_intel.consensus else None,
        n_bookmakers=primary_intel.n_bookmakers if primary_intel else 0,
        freshness=case.primary_alert.freshness,
        neighbouring_thresholds=neighbours, curve_is_monotonic=curve_monotonic, curve_has_jumps=curve_jumps,
        root_cause=root_cause, research_category=research_category, notes=notes,
    )
