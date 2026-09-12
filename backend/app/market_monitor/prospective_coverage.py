"""Prospective Coverage panel (Genuine Prospective Operation stage, item 4):
purely descriptive, operational-health view of the freeze/follow-up
pipeline itself - is the system actually watching upcoming matches and
following them through - as distinct from effectiveness.py's OUTCOME
metrics (were the alerts useful). Read-only, no detection/threshold logic.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.market_monitor.detector import active_match_ids
from app.models import AnomalyCaseFollowUp, AnomalyCaseSnapshot


@dataclass(frozen=True)
class ProspectiveCoverage:
    n_upcoming_matches_monitored: int
    n_frozen_cases: int  # currently-open (unresolved) genuinely prospective High/Critical cases
    n_cases_with_2plus_followups: int
    n_cases_with_3plus_followups: int
    earliest_hours_before_kickoff_captured: float | None  # furthest-out follow-up ever captured
    latest_pre_kickoff_capture_hours: float | None  # closest-to-kickoff follow-up ever captured


def compute_prospective_coverage(db: Session, *, boundary: datetime | None = None) -> ProspectiveCoverage:
    """`boundary`: the formal PRODUCTION_PROSPECTIVE_TRACKING_START cutoff
    (see app/prospective_boundary.py). Deliberately applied to only PART of
    this dataclass:

    - n_upcoming_matches_monitored and n_frozen_cases (currently-open,
      unresolved prospective cases) are CURRENT OPERATIONAL STATE - "is the
      system watching right now" / "how many cases need attention right
      now" - and are never boundary-filtered, matching this module's own
      docstring framing as an operational-health view, distinct from
      effectiveness.py's outcome metrics.
    - The accumulated follow-up metrics (n_cases_with_2plus_followups,
      n_cases_with_3plus_followups, and the timing extremes) DO respect the
      boundary. Eligibility is determined by the PARENT
      AnomalyCaseSnapshot.frozen_at, never by AnomalyCaseFollowUp's own
      captured_at - a follow-up captured after the boundary for a case
      frozen BEFORE the boundary still belongs to that pre-boundary case
      and must not leak into the formal post-boundary accumulated metrics.
    """
    n_upcoming = len(active_match_ids(db))

    prospective = db.scalars(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.capture_mode == "prospective")).all()
    open_prospective = [s for s in prospective if s.resolved_at is None]

    # A SQL-level filter, not an in-Python comparison against the
    # already-loaded `prospective` objects above - SQLite's driver returns
    # DateTime(timezone=True) values as timezone-NAIVE on round-trip
    # (a documented limitation this codebase already works around
    # elsewhere, e.g. real_market_tracking.py's hours_before()), so a
    # Python-level `s.frozen_at >= boundary` comparison would raise
    # "can't compare offset-naive and offset-aware datetimes" there, even
    # though the underlying data is genuinely UTC throughout. Filtering in
    # SQL lets the database handle this correctly regardless of dialect.
    eligible_stmt = select(AnomalyCaseSnapshot.id).where(AnomalyCaseSnapshot.capture_mode == "prospective")
    if boundary is not None:
        eligible_stmt = eligible_stmt.where(AnomalyCaseSnapshot.frozen_at >= boundary)
    boundary_eligible_ids = set(db.scalars(eligible_stmt).all())

    followup_counts = dict(db.execute(select(AnomalyCaseFollowUp.snapshot_id, func.count()).group_by(AnomalyCaseFollowUp.snapshot_id)).all())
    n_2plus = sum(1 for sid, n in followup_counts.items() if sid in boundary_eligible_ids and n >= 2)
    n_3plus = sum(1 for sid, n in followup_counts.items() if sid in boundary_eligible_ids and n >= 3)

    hours_stmt = (
        select(AnomalyCaseFollowUp.hours_to_kickoff)
        .join(AnomalyCaseSnapshot, AnomalyCaseFollowUp.snapshot_id == AnomalyCaseSnapshot.id)
        .where(AnomalyCaseSnapshot.capture_mode == "prospective")
    )
    if boundary is not None:
        hours_stmt = hours_stmt.where(AnomalyCaseSnapshot.frozen_at >= boundary)
    hours = db.scalars(hours_stmt).all()

    return ProspectiveCoverage(
        n_upcoming_matches_monitored=n_upcoming,
        n_frozen_cases=len(open_prospective),
        n_cases_with_2plus_followups=n_2plus,
        n_cases_with_3plus_followups=n_3plus,
        earliest_hours_before_kickoff_captured=max(hours) if hours else None,
        latest_pre_kickoff_capture_hours=min(hours) if hours else None,
    )
