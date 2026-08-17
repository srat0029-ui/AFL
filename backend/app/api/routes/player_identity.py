"""Player identity management API — Sections 8-9 of the live-operations
stage brief: safely adding a genuinely new 2026 debutant (no historical
data fabricated) and managing the explicit, reviewable player-alias table.
Both are manual, human-triggered actions; neither is ever called
automatically by ingestion.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    DuplicateNameWarningRead,
    NewPlayerCreate,
    NewPlayerRead,
    PlayerAliasCreate,
    PlayerAliasRead,
)
from app.database import get_db
from app.models import Player, PlayerAlias
from app.player_modelling.player_identity import (
    DuplicateNameWarning,
    add_player_alias,
    create_new_player,
    delete_player_alias,
    list_player_aliases,
)

router = APIRouter(prefix="/api/afl", tags=["player-identity"])


@router.post("/players/new", response_model=NewPlayerRead)
def post_new_player(payload: NewPlayerCreate, db: Session = Depends(get_db)):
    """Section 8: create a Player row with zero historical games for a
    genuine 2026 debutant. Returns 409 (not the player) if a player with
    this exact display_name already exists somewhere, unless force=True —
    this is a safety net against accidentally creating a duplicate, not a
    hard block, since same-name debutants are a real (if rare) possibility."""
    try:
        result = create_new_player(db, display_name=payload.display_name, team_id=payload.team_id, note=payload.note, force=payload.force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(result, DuplicateNameWarning):
        raise HTTPException(
            status_code=409,
            detail=DuplicateNameWarningRead(
                existing_player_id=result.existing_player_id,
                existing_player_source=result.existing_player_source,
                existing_player_team_id=result.existing_player_team_id,
            ).model_dump(),
        )
    return NewPlayerRead(
        id=result.id, display_name=result.display_name, current_team_id=result.current_team_id,
        source=result.source, is_active=result.is_active,
    )


def _alias_read(alias: PlayerAlias) -> PlayerAliasRead:
    return PlayerAliasRead(
        id=alias.id, player_id=alias.player_id, player_name=alias.player.display_name,
        alias_name=alias.alias_name, source=alias.source, note=alias.note, created_at=alias.created_at,
    )


@router.get("/player-aliases", response_model=list[PlayerAliasRead])
def get_player_aliases(player_id: int | None = None, db: Session = Depends(get_db)) -> list[PlayerAliasRead]:
    return [_alias_read(a) for a in list_player_aliases(db, player_id=player_id)]


@router.post("/player-aliases", response_model=PlayerAliasRead)
def post_player_alias(payload: PlayerAliasCreate, db: Session = Depends(get_db)) -> PlayerAliasRead:
    if db.get(Player, payload.player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    existing = db.scalar(select(PlayerAlias).where(PlayerAlias.alias_name == payload.alias_name, PlayerAlias.source == payload.source))
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Alias {payload.alias_name!r} (source={payload.source}) already maps to player {existing.player_id}")
    alias = add_player_alias(db, player_id=payload.player_id, alias_name=payload.alias_name, source=payload.source, note=payload.note)
    return _alias_read(alias)


@router.delete("/player-aliases/{alias_id}", status_code=204)
def delete_player_alias_route(alias_id: int, db: Session = Depends(get_db)) -> None:
    if not delete_player_alias(db, alias_id):
        raise HTTPException(status_code=404, detail="Alias not found")
