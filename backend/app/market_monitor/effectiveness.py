"""B2B effectiveness dashboard (items 7-8): purely descriptive aggregation
over already-settled AnomalyCaseSnapshot rows — no new detection, no
retuning, nothing here changes a threshold/weight/probability (item 9's
explicit boundary). Every rate is reported alongside its sample size, and
any denominator below EARLY_EVIDENCE_MIN_N is flagged so a reader never
mistakes a handful of settled cases for a stable statistic.
"""

import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market_monitor.outcome_taxonomy import (
    MARKET_MOVED_AWAY_FROM_MODEL,
    MARKET_MOVED_TOWARD_MODEL,
    PERSISTED_TO_KICKOFF,
    INCONCLUSIVE,
)
from app.market_monitor.types import (
    ADJACENT_THRESHOLD_JUMP,
    BOOKMAKER_MOVED_VS_STABLE_CONSENSUS,
    BOOKMAKER_VS_CONSENSUS_OUTLIER,
    CONSENSUS_MOVED_VS_STALE_BOOKMAKER,
    LARGE_MARKET_DISPERSION,
    MODEL_VS_MARKET_DIVERGENCE,
    NON_MONOTONIC_PLAYER_PRICE_CURVE,
    SHARP_MARKET_MOVE_MODEL_STABLE,
    STALE_AFTER_CONTEXT_CHANGE,
    STALE_AFTER_LINEUP_CHANGE,
)
from app.models import AnomalyCaseSnapshot

EARLY_EVIDENCE_MIN_N = 10

# item 8's six alert-type families, mapped to the underlying alert_types
# codes that can appear in a case's frozen alert_types list.
ALERT_TYPE_FAMILIES: dict[str, tuple[str, ...]] = {
    "model_vs_market_divergence": (MODEL_VS_MARKET_DIVERGENCE,),
    "bookmaker_outlier": (BOOKMAKER_VS_CONSENSUS_OUTLIER,),
    "stale_after_context": (STALE_AFTER_LINEUP_CHANGE, STALE_AFTER_CONTEXT_CHANGE),
    "curve_anomaly": (NON_MONOTONIC_PLAYER_PRICE_CURVE, ADJACENT_THRESHOLD_JUMP),
    "dispersion": (LARGE_MARKET_DISPERSION,),
    "movement_anomaly": (SHARP_MARKET_MOVE_MODEL_STABLE, BOOKMAKER_MOVED_VS_STABLE_CONSENSUS, CONSENSUS_MOVED_VS_STALE_BOOKMAKER),
}


def _pct(n_num: int, n_den: int) -> float | None:
    return (n_num / n_den * 100.0) if n_den else None


@dataclass(frozen=True)
class EffectivenessSummary:
    n_frozen_cases: int
    n_unique_markets: int
    n_resolved: int
    sample_label: str  # "Early evidence" below EARLY_EVIDENCE_MIN_N resolved cases, else ""

    pct_outlier_converged: float | None
    n_outlier_eligible: int
    pct_consensus_moved_toward_model: float | None
    pct_consensus_moved_away_from_model: float | None
    pct_stale_context_repriced: float | None
    n_stale_context_eligible: int
    median_time_to_resolution_hours: float | None
    pct_persisted_to_kickoff: float | None


@dataclass(frozen=True)
class AlertTypeEffectiveness:
    alert_type_family: str
    n_resolved: int
    sample_label: str
    pct_market_moved_toward_model: float | None
    pct_market_moved_away_from_model: float | None
    pct_persisted_to_kickoff: float | None
    pct_inconclusive: float | None


def _sample_label(n: int) -> str:
    return "Early evidence" if n < EARLY_EVIDENCE_MIN_N else ""


def compute_effectiveness_summary(db: Session) -> EffectivenessSummary:
    all_snaps = db.scalars(select(AnomalyCaseSnapshot)).all()
    resolved = [s for s in all_snaps if s.resolved_at is not None]
    n_resolved = len(resolved)

    outlier_eligible = [s for s in resolved if s.outlier_converged is not None]
    stale_eligible = [s for s in resolved if s.stale_market_repriced is not None]
    resolution_hours = [s.time_to_resolution_hours for s in resolved if s.time_to_resolution_hours is not None]

    return EffectivenessSummary(
        n_frozen_cases=len(all_snaps),
        n_unique_markets=len({s.market_type for s in all_snaps}),
        n_resolved=n_resolved,
        sample_label=_sample_label(n_resolved),
        pct_outlier_converged=_pct(sum(1 for s in outlier_eligible if s.outlier_converged), len(outlier_eligible)),
        n_outlier_eligible=len(outlier_eligible),
        pct_consensus_moved_toward_model=_pct(sum(1 for s in resolved if MARKET_MOVED_TOWARD_MODEL in (s.outcome_codes or [])), n_resolved),
        pct_consensus_moved_away_from_model=_pct(sum(1 for s in resolved if MARKET_MOVED_AWAY_FROM_MODEL in (s.outcome_codes or [])), n_resolved),
        pct_stale_context_repriced=_pct(sum(1 for s in stale_eligible if s.stale_market_repriced), len(stale_eligible)),
        n_stale_context_eligible=len(stale_eligible),
        median_time_to_resolution_hours=statistics.median(resolution_hours) if resolution_hours else None,
        pct_persisted_to_kickoff=_pct(sum(1 for s in resolved if PERSISTED_TO_KICKOFF in (s.outcome_codes or [])), n_resolved),
    )


def compute_alert_type_effectiveness(db: Session) -> list[AlertTypeEffectiveness]:
    resolved = db.scalars(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.resolved_at.is_not(None))).all()
    out = []
    for family, codes in ALERT_TYPE_FAMILIES.items():
        cases = [s for s in resolved if any(c in (s.alert_types or []) for c in codes)]
        n = len(cases)
        out.append(AlertTypeEffectiveness(
            alert_type_family=family, n_resolved=n, sample_label=_sample_label(n),
            pct_market_moved_toward_model=_pct(sum(1 for s in cases if MARKET_MOVED_TOWARD_MODEL in (s.outcome_codes or [])), n),
            pct_market_moved_away_from_model=_pct(sum(1 for s in cases if MARKET_MOVED_AWAY_FROM_MODEL in (s.outcome_codes or [])), n),
            pct_persisted_to_kickoff=_pct(sum(1 for s in cases if PERSISTED_TO_KICKOFF in (s.outcome_codes or [])), n),
            pct_inconclusive=_pct(sum(1 for s in cases if INCONCLUSIVE in (s.outcome_codes or [])), n),
        ))
    return out
