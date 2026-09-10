"""Player context analysis - with/without-teammate splits, an honestly
gated adjusted effect, full evidence, and tag-watch status (the Player
Context Research product direction). This router is deliberately thin -
all computation lives in app/player_modelling/player_context_analysis.py
and app/player_modelling/tag_watch.py.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.schemas import PlayerContextAnalysisRead
from app.database import get_db
from app.player_modelling.player_context_analysis import (
    SUPPORTED_STATS,
    build_player_context_analysis,
    player_context_analysis_as_dict,
)
from app.player_modelling.tag_watch import tag_watch_as_dict, tag_watch_for_player

router = APIRouter(prefix="/api/afl", tags=["player-context"])


def _parse_thresholds(raw: str | None) -> tuple[int, ...] | None:
    if raw is None:
        return None
    try:
        parsed = tuple(sorted({int(t.strip()) for t in raw.split(",") if t.strip()}))
    except ValueError:
        raise HTTPException(status_code=400, detail="thresholds must be a comma-separated list of integers.")
    return parsed or None


@router.get("/players/{player_id}/context/{teammate_id}", response_model=PlayerContextAnalysisRead)
def get_player_context_analysis(
    player_id: int,
    teammate_id: int,
    stat: str = Query("disposals", description="Which stat to analyse - one of: " + ", ".join(sorted(SUPPORTED_STATS))),
    thresholds: str | None = Query(None, description="Comma-separated milestone thresholds, e.g. '15,20,25'."),
    db: Session = Depends(get_db),
) -> PlayerContextAnalysisRead:
    if player_id == teammate_id:
        raise HTTPException(status_code=400, detail="player_id and teammate_id must be different players.")
    if stat not in SUPPORTED_STATS:
        raise HTTPException(status_code=400, detail=f"Unsupported stat {stat!r} - must be one of {sorted(SUPPORTED_STATS)}.")

    parsed_thresholds = _parse_thresholds(thresholds)

    try:
        analysis = build_player_context_analysis(db, player_id, teammate_id, stat=stat, thresholds=parsed_thresholds)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    result = player_context_analysis_as_dict(analysis)
    result["tag_watch"] = tag_watch_as_dict(tag_watch_for_player(db, player_id))
    return PlayerContextAnalysisRead(**result)
