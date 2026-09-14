"""Prospective Evidence Center (/api/v1/prospective-evidence-center) — a
single, read-only composition of every existing formal prospective dataset
in the system, so the evidence used to judge whether the genuinely
prospective system works can be inspected from one place in the product
instead of stitched together by hand across four separate API tabs (Model
Registry, SGM prospective evaluation, Real Market Tracking, Market Monitor
effectiveness).

Deliberately computes nothing new: every field below comes from calling the
exact same route functions the existing four pages already use
(app.api.routes.model_registry_v1, real_market_tracking, market_monitor_v1),
each of which threads the identical `prospective_tracking_start()` boundary
value down to its own loader (app.player_modelling.prospective_evaluation /
sgm_prospective_evaluation / real_market_tracking,
app.market_monitor.effectiveness / prospective_coverage). This module reads
that same boundary exactly once, purely to describe it to the caller — it is
never re-derived or recomputed, so this page can never drift from, double
count, or contradict what those four pages already report individually.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.market_monitor_schemas import EffectivenessDashboardRead
from app.api.pricing_schemas import ProspectiveEvaluationRead, SgmProspectiveEvaluationRead
from app.api.routes.market_monitor_v1 import get_effectiveness
from app.api.routes.model_registry_v1 import get_prospective_evaluation, get_sgm_prospective_evaluation
from app.api.routes.real_market_tracking import get_real_market_tracking
from app.api.schemas import RealMarketTrackingReportRead, UtcDatetime
from app.database import get_db
from app.prospective_boundary import prospective_tracking_start

router = APIRouter(prefix="/api/v1/prospective-evidence-center", tags=["prospective-evidence-center-v1"])


class ProspectiveBoundaryRead(BaseModel):
    boundary_active: bool
    tracking_start_at: UtcDatetime | None
    environment_note: str


class ProspectiveEvidenceCenterRead(BaseModel):
    generated_at: UtcDatetime
    boundary: ProspectiveBoundaryRead
    pricing_evaluation: ProspectiveEvaluationRead
    sgm_evaluation: SgmProspectiveEvaluationRead
    real_market_tracking: RealMarketTrackingReportRead
    market_monitor: EffectivenessDashboardRead


@router.get("", response_model=ProspectiveEvidenceCenterRead)
def get_prospective_evidence_center(db: Session = Depends(get_db)) -> ProspectiveEvidenceCenterRead:
    boundary = prospective_tracking_start()
    return ProspectiveEvidenceCenterRead(
        generated_at=datetime.now(timezone.utc),
        boundary=ProspectiveBoundaryRead(
            boundary_active=boundary is not None,
            tracking_start_at=boundary,
            environment_note=(
                "Formal prospective boundary is active in this environment — every figure below is scoped to "
                "predictions, prices, and cases frozen on or after this timestamp."
                if boundary is not None
                else "This environment has no formal prospective boundary configured (the boundary is "
                "production-only — see app/prospective_boundary.py). The evidence below reports the full "
                "available history rather than a boundary-scoped formal sample."
            ),
        ),
        # Same route functions the existing pages call — see module docstring.
        pricing_evaluation=get_prospective_evaluation(db),
        sgm_evaluation=get_sgm_prospective_evaluation(db),
        real_market_tracking=get_real_market_tracking(match_id=None, market=None, db=db),
        market_monitor=get_effectiveness(db),
    )
