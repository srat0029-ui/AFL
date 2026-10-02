"""NBA API — everything NBA is served under /api/nba, in its own route
module. AFL route modules are never given an `if sport == ...` branch.

Read-only: nothing here triggers a provider request.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.nba_schemas import NbaAvailabilityAsOfRead, NbaStatusRead
from app.core.prospective import ensure_utc
from app.database import get_db
from app.models.nba import NbaPlayer
from app.nba.asof import availability_known_at
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


@router.get("/players/{player_id}/availability", response_model=NbaAvailabilityAsOfRead)
def get_player_availability(player_id: int, as_of: datetime | None = None, db: Session = Depends(get_db)) -> NbaAvailabilityAsOfRead:
    """What the system knew about a player's availability at `as_of`
    (default: now) - the latest injury-feed observation made at or before
    that instant, never anything observed later. A timestamp without a zone
    is read as UTC."""
    player = db.get(NbaPlayer, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail=f"No NBA player with id {player_id}.")
    cutoff = ensure_utc(as_of) if as_of is not None else datetime.now(timezone.utc)
    evidence = availability_known_at(db, player_id, cutoff)
    obs = evidence.observation
    return NbaAvailabilityAsOfRead(
        player_id=player.id,
        player_name=player.display_name,
        source_player_id=player.source_player_id,
        as_of=cutoff,
        observation=None if obs is None else {
            **{name: getattr(obs, name) for name in (
                "is_listed", "source_status", "status", "injury_type", "injury_location", "injury_side", "injury_detail", "fantasy_status",
                "expected_return_date", "short_comment", "team_id", "source",
            )},
            "source_published_at": ensure_utc(obs.source_published_at) if obs.source_published_at else None,
            "observed_at": ensure_utc(obs.observed_at),
        },
        first_observed_at=evidence.first_observed_at,
        last_confirmed_at=evidence.last_confirmed_at,
    )
