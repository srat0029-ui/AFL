from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import MarketEdgeRead
from app.database import get_db
from app.edges.calculator import ModelsUnavailableError, build_model_context, compute_match_edges
from app.models import Match

router = APIRouter(prefix="/api/matches/{match_id}/edges", tags=["edges"])


@router.get("", response_model=list[MarketEdgeRead])
def get_match_edges(match_id: int, db: Session = Depends(get_db)) -> list[MarketEdgeRead]:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    try:
        context = build_model_context(db)
    except ModelsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    edges = compute_match_edges(db, match, context)
    return [MarketEdgeRead.model_validate(e) for e in edges]
