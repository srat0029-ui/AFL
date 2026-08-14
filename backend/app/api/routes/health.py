from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Sport

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health() -> dict:
    """Liveness check: the API process is up. Does not touch the database."""
    return {"status": "ok"}


@router.get("/db")
def health_db(db: Session = Depends(get_db)) -> dict:
    """Readiness check: the API can reach and query the database.

    This is the endpoint the frontend calls for the Stage 0
    frontend -> API -> database round trip.
    """
    sport_count = db.scalar(select(func.count()).select_from(Sport))
    return {"status": "ok", "database": "connected", "sport_rows": sport_count}
