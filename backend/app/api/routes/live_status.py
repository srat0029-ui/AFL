"""Live Status API — Sections 5-6, 18 of the live-operations stage brief.
Read-only: every endpoint here only ever reads already-persisted state and
NEVER triggers an external request (paid or free). Refreshing data is a
deliberately separate, explicit CLI action (`run-live-cycle` /
`refresh-prop-odds` / `settle-props`) — see live_cycle.py and Section 19's
"do not allow accidental repeated provider requests from double-clicking or
page refresh."
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import LiveStatusReportRead
from app.database import get_db
from app.player_modelling.live_status import load_live_status

router = APIRouter(prefix="/api/afl", tags=["live-status"])


@router.get("/live-status", response_model=LiveStatusReportRead)
def get_live_status(db: Session = Depends(get_db)) -> LiveStatusReportRead:
    report = load_live_status(db)
    return LiveStatusReportRead(
        round_summary=report.round_summary.__dict__,
        matches=[{**{k: v for k, v in m.__dict__.items() if k != "diagnosis"}, "diagnosis": m.diagnosis.__dict__} for m in report.matches],
        recent_runs=[
            {
                "id": r.id, "run_at": r.run_at, "finished_at": r.finished_at, "overall_status": r.overall_status,
                "steps": r.steps, "odds_credits_consumed": r.odds_credits_consumed, "odds_credits_remaining": r.odds_credits_remaining,
                "matches_affected": r.matches_affected, "quotes_added": r.quotes_added,
                "observations_added": r.observations_added, "observations_settled": r.observations_settled,
            }
            for r in report.recent_runs
        ],
    )
