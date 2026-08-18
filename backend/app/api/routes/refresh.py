"""Explicit, user-triggered data refresh (product-polish stage). This is the
ONLY endpoint in this API allowed to trigger an external (potentially paid)
provider request — deliberately a POST, never a GET, so a page load or
double-click-driven navigation can never fire it (Section 19's "do not
allow accidental repeated provider requests from double-clicking or page
refresh" — see live_status.py's read-only guarantee on every other AFL
status/freshness endpoint). Reuses run_live_cycle exactly as the
`run-live-cycle` CLI does — no ingestion logic is duplicated here, and
every provider call inside it already respects its own quota/refresh-
interval policy (prop_odds_quota.py), so a second click while one run is
still in flight is rejected rather than queued.
"""

import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routes.live_status import live_cycle_run_dict
from app.api.schemas import LiveCycleRunRead
from app.database import get_db
from app.player_modelling.live_cycle import run_live_cycle

router = APIRouter(prefix="/api/afl", tags=["refresh"])

_refresh_lock = threading.Lock()


@router.post("/refresh", response_model=LiveCycleRunRead)
def trigger_refresh(db: Session = Depends(get_db)) -> LiveCycleRunRead:
    if not _refresh_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A data refresh is already running — please wait for it to finish.")
    try:
        run = run_live_cycle(db)
    finally:
        _refresh_lock.release()
    return LiveCycleRunRead(**live_cycle_run_dict(run))
