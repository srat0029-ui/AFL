"""NBA API — everything NBA is served under /api/nba, in its own route
module. AFL route modules are never given an `if sport == ...` branch.

Read-only: nothing here triggers a provider request.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.nba_schemas import NbaStatusRead
from app.database import get_db
from app.nba.status import load_nba_status

router = APIRouter(prefix="/api/nba", tags=["nba"])


@router.get("/status", response_model=NbaStatusRead)
def get_nba_status(db: Session = Depends(get_db)) -> NbaStatusRead:
    """What NBA data exists right now: row counts per dataset and how many
    predictions have been frozen, closed and settled."""
    report = load_nba_status(db)
    return NbaStatusRead(
        sport=report.sport,
        markets=report.markets,
        datasets=[d.__dict__ for d in report.datasets],
        predictions_frozen=report.predictions_frozen,
        predictions_with_closing_line=report.predictions_with_closing_line,
        predictions_settled=report.predictions_settled,
    )
