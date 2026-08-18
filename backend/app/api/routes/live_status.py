"""Live Status API — Sections 5-6, 18 of the live-operations stage brief.
Read-only: every endpoint here only ever reads already-persisted state and
NEVER triggers an external request (paid or free). Refreshing data is a
deliberately separate, explicit action — the CLI (`run-live-cycle` /
`refresh-prop-odds` / `settle-props`) or the frontend's single "Refresh
Data" button (see app/api/routes/refresh.py, the only POST endpoint allowed
to trigger a provider request) — see live_cycle.py and Section 19's "do not
allow accidental repeated provider requests from double-clicking or page
refresh."
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import DataFreshnessReportRead, LiveStatusReportRead
from app.database import get_db
from app.models import LiveCycleRun
from app.player_modelling.data_freshness import load_data_freshness
from app.player_modelling.live_status import load_live_status

router = APIRouter(prefix="/api/afl", tags=["live-status"])


def live_cycle_run_dict(r: LiveCycleRun) -> dict:
    """Shared dict shape for LiveCycleRunRead — used both for the read-only
    recent-runs history here and for refresh.py's just-triggered run."""
    return {
        "id": r.id, "run_at": r.run_at, "finished_at": r.finished_at, "overall_status": r.overall_status,
        "steps": r.steps, "odds_credits_consumed": r.odds_credits_consumed, "odds_credits_remaining": r.odds_credits_remaining,
        "matches_affected": r.matches_affected, "quotes_added": r.quotes_added,
        "observations_added": r.observations_added, "observations_settled": r.observations_settled,
        "team_odds_quotes_added": r.team_odds_quotes_added, "weather_snapshots_added": r.weather_snapshots_added,
    }


@router.get("/live-status", response_model=LiveStatusReportRead)
def get_live_status(db: Session = Depends(get_db)) -> LiveStatusReportRead:
    report = load_live_status(db)
    return LiveStatusReportRead(
        round_summary=report.round_summary.__dict__,
        matches=[{**{k: v for k, v in m.__dict__.items() if k != "diagnosis"}, "diagnosis": m.diagnosis.__dict__} for m in report.matches],
        recent_runs=[live_cycle_run_dict(r) for r in report.recent_runs],
    )


@router.get("/data-freshness", response_model=DataFreshnessReportRead)
def get_data_freshness(db: Session = Depends(get_db)) -> DataFreshnessReportRead:
    report = load_data_freshness(db)
    return DataFreshnessReportRead(items=[item.__dict__ for item in report.items])
