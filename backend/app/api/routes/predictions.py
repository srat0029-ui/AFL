from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import MatchPredictionsRead
from app.database import get_db
from app.edges.calculator import ModelsUnavailableError, build_model_context, compute_match_predictions
from app.models import Match

router = APIRouter(prefix="/api/matches/{match_id}/predictions", tags=["predictions"])


@router.get("", response_model=MatchPredictionsRead)
def get_match_predictions(match_id: int, db: Session = Depends(get_db)) -> MatchPredictionsRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    try:
        context = build_model_context(db)
    except ModelsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    predictions = compute_match_predictions(match, context)
    return MatchPredictionsRead.model_validate(predictions)
