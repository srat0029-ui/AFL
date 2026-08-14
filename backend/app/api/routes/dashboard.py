from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.matches import _to_summary
from app.api.schemas import DashboardEntry, MarketEdgeRead, MatchPredictionsRead
from app.database import get_db
from app.edges.calculator import (
    ModelsUnavailableError,
    best_edge,
    build_model_context,
    compute_match_edges,
    compute_match_predictions,
)
from app.models import Match, MatchStatus, Sport

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=list[DashboardEntry])
def get_dashboard(sport: str = "AFL", db: Session = Depends(get_db)) -> list[DashboardEntry]:
    matches = db.scalars(
        select(Match)
        .join(Sport)
        .where(Sport.code == sport, Match.status == MatchStatus.SCHEDULED)
        .order_by(Match.scheduled_start)
    ).all()

    if not matches:
        return []

    try:
        context = build_model_context(db)
    except ModelsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    entries = []
    for match in matches:
        predictions = compute_match_predictions(match, context)
        edges = compute_match_edges(db, match, context)
        top_edge = best_edge(edges)
        entries.append(
            DashboardEntry(
                match=_to_summary(match),
                predictions=MatchPredictionsRead.model_validate(predictions),
                best_edge=MarketEdgeRead.model_validate(top_edge) if top_edge else None,
            )
        )

    return entries
