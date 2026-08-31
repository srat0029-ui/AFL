import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Sport
from app.release_info import get_release_info

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])
release_router = APIRouter(tags=["health"])


@router.get("")
def health() -> dict:
    """Liveness check: the API process is up. Does not touch the database.

    A platform's process-liveness probe should point here, never at /db or
    /v1/pricing/readiness - a transient DB blip or a stale bookmaker feed
    is not a reason to restart an otherwise-healthy process."""
    return {"status": "ok"}


@router.get("/db")
def health_db(response: Response, db: Session = Depends(get_db)) -> dict:
    """Readiness check: the API can reach and query the database.

    This is the endpoint the frontend calls for the Stage 0
    frontend -> API -> database round trip, and the one a platform's
    readiness probe should point at. Degrades to a clear 503 on a DB
    failure instead of falling through to a generic 500 - so "database is
    down" is distinguishable from "the application has a bug."
    """
    try:
        sport_count = db.scalar(select(func.count()).select_from(Sport))
    except SQLAlchemyError:
        logger.error("health_db.database_unreachable", exc_info=True)
        response.status_code = 503
        return {"status": "error", "database": "unreachable"}
    return {"status": "ok", "database": "connected", "sport_rows": sport_count}


@release_router.get("/api/release")
def release(settings: Settings = Depends(get_settings)) -> dict:
    """Deployment provenance: which exact code is running. Safe fields
    only - no filesystem paths, no internal build detail."""
    info = get_release_info()
    return {"git_sha": info.git_sha, "build_time": info.build_time, "app_version": "0.1.0", "app_env": settings.app_env}
