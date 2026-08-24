"""Prospective Coverage panel (Genuine Prospective Operation stage, item 4):
purely descriptive, operational-health view of the freeze/follow-up
pipeline itself - is the system actually watching upcoming matches and
following them through - as distinct from effectiveness.py's OUTCOME
metrics (were the alerts useful). Read-only, no detection/threshold logic.
"""

from dataclasses import dataclass

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


def compute_prospective_coverage(db: Session) -> ProspectiveCoverage:
    n_upcoming = len(active_match_ids(db))

    prospective = db.scalars(select(AnomalyCaseSnapshot).where(AnomalyCaseSnapshot.capture_mode == "prospective")).all()
    open_prospective = [s for s in prospective if s.resolved_at is None]
    prospective_ids = {s.id for s in prospective}

    followup_counts = dict(db.execute(select(AnomalyCaseFollowUp.snapshot_id, func.count()).group_by(AnomalyCaseFollowUp.snapshot_id)).all())
    n_2plus = sum(1 for sid, n in followup_counts.items() if sid in prospective_ids and n >= 2)
    n_3plus = sum(1 for sid, n in followup_counts.items() if sid in prospective_ids and n >= 3)

    hours = db.scalars(
        select(AnomalyCaseFollowUp.hours_to_kickoff)
        .join(AnomalyCaseSnapshot, AnomalyCaseFollowUp.snapshot_id == AnomalyCaseSnapshot.id)
        .where(AnomalyCaseSnapshot.capture_mode == "prospective")
    ).all()

    return ProspectiveCoverage(
        n_upcoming_matches_monitored=n_upcoming,
        n_frozen_cases=len(open_prospective),
        n_cases_with_2plus_followups=n_2plus,
        n_cases_with_3plus_followups=n_3plus,
        earliest_hours_before_kickoff_captured=max(hours) if hours else None,
        latest_pre_kickoff_capture_hours=min(hours) if hours else None,
    )
