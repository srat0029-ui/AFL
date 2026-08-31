"""SGM monitoring — reads `SgmPriceSnapshot` directly (see that model's
docstring). No new persistence: the multi-horizon snapshot history already
IS the historical observation set this needs; this module is a query/
aggregation layer over it, not a second detection engine.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SgmDependenceCoefficient, SgmPriceSnapshot
from app.trading_monitor.thresholds import CORRELATION_ADJUSTMENT_BUCKETS, SGM_MOVEMENT_NOISE_MULTIPLE

RECENT_SNAPSHOT_LIMIT = 200  # a fixed operational window, not a statistical threshold - keeps the query bounded


@dataclass(frozen=True)
class SgmDivergenceEntry:
    match_id: int
    leg_signature: str
    n_legs: int
    model_probability: float
    naive_independence_probability: float
    naive_vs_joint_difference_pp: float
    correlation_adjustment_pp: float
    correlation_adjustment_bucket: str
    snapshot_horizon: str
    generated_at: datetime


@dataclass(frozen=True)
class SgmHorizonMovement:
    match_id: int
    leg_signature: str
    n_legs: int
    earliest_horizon: str
    earliest_probability: float
    latest_horizon: str
    latest_probability: float
    absolute_change: float
    is_beyond_mc_noise: bool


@dataclass(frozen=True)
class SgmCoefficientProvenance:
    market: str
    slope: float
    intercept: float
    n_observations: int
    model_version: str
    fitted_at: datetime


@dataclass(frozen=True)
class SgmMonitoringReport:
    n_recent_snapshots: int
    largest_naive_vs_joint_differences: list[SgmDivergenceEntry]
    largest_correlation_adjustments: list[SgmDivergenceEntry]
    horizon_movements: list[SgmHorizonMovement]
    coefficient_provenance: list[SgmCoefficientProvenance]


def _bucket_for(magnitude_pp: float) -> str:
    for lo, hi, label in CORRELATION_ADJUSTMENT_BUCKETS:
        if lo <= magnitude_pp < hi:
            return label
    return CORRELATION_ADJUSTMENT_BUCKETS[-1][2]


def _divergence_entry(s: SgmPriceSnapshot) -> SgmDivergenceEntry:
    diff_pp = (s.model_probability - s.naive_independence_probability) * 100.0
    return SgmDivergenceEntry(
        match_id=s.match_id, leg_signature=s.leg_signature, n_legs=s.n_legs, model_probability=s.model_probability,
        naive_independence_probability=s.naive_independence_probability, naive_vs_joint_difference_pp=diff_pp,
        correlation_adjustment_pp=s.correlation_adjustment_pp, correlation_adjustment_bucket=_bucket_for(abs(s.correlation_adjustment_pp)),
        snapshot_horizon=s.snapshot_horizon, generated_at=s.generated_at,
    )


def _horizon_movements(snapshots: list[SgmPriceSnapshot]) -> list[SgmHorizonMovement]:
    groups: dict[tuple[int, str], list[SgmPriceSnapshot]] = {}
    for s in snapshots:
        groups.setdefault((s.match_id, s.leg_signature), []).append(s)

    movements = []
    for (match_id, signature), group in groups.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda s: s.hours_to_kickoff, reverse=True)  # earliest (largest hours_to_kickoff) first
        earliest, latest = ordered[0], ordered[-1]
        absolute_change = latest.model_probability - earliest.model_probability
        combined_se = (earliest.mc_standard_error ** 2 + latest.mc_standard_error ** 2) ** 0.5
        movements.append(SgmHorizonMovement(
            match_id=match_id, leg_signature=signature, n_legs=latest.n_legs,
            earliest_horizon=earliest.snapshot_horizon, earliest_probability=earliest.model_probability,
            latest_horizon=latest.snapshot_horizon, latest_probability=latest.model_probability,
            absolute_change=absolute_change, is_beyond_mc_noise=abs(absolute_change) >= SGM_MOVEMENT_NOISE_MULTIPLE * combined_se,
        ))
    movements.sort(key=lambda m: (not m.is_beyond_mc_noise, -abs(m.absolute_change)))
    return movements


def load_sgm_monitoring(db: Session, *, limit: int = 20) -> SgmMonitoringReport:
    snapshots = db.scalars(
        select(SgmPriceSnapshot).order_by(SgmPriceSnapshot.generated_at.desc()).limit(RECENT_SNAPSHOT_LIMIT)
    ).all()

    by_diff = sorted(snapshots, key=lambda s: abs(s.model_probability - s.naive_independence_probability), reverse=True)[:limit]
    by_adjustment = sorted(snapshots, key=lambda s: abs(s.correlation_adjustment_pp), reverse=True)[:limit]

    coefficients = db.scalars(select(SgmDependenceCoefficient)).all()

    return SgmMonitoringReport(
        n_recent_snapshots=len(snapshots),
        largest_naive_vs_joint_differences=[_divergence_entry(s) for s in by_diff],
        largest_correlation_adjustments=[_divergence_entry(s) for s in by_adjustment],
        horizon_movements=_horizon_movements(list(snapshots))[:limit],
        coefficient_provenance=[
            SgmCoefficientProvenance(
                market=c.market, slope=c.slope, intercept=c.intercept, n_observations=c.n_observations,
                model_version=c.model_version, fitted_at=c.fitted_at,
            )
            for c in coefficients
        ],
    )
