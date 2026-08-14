from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.match_queries import query_matches
from app.api.schemas import MatchSummary, TeamSummary, VenueSummary
from app.database import get_db
from app.models import Match, MatchStatus

router = APIRouter(prefix="/api/matches", tags=["matches"])


def _to_summary(match: Match) -> MatchSummary:
    return MatchSummary(
        id=match.id,
        season_year=match.season.year,
        round_number=match.round.round_number,
        status=match.status.value,
        scheduled_start=match.scheduled_start,
        home_team=TeamSummary.model_validate(match.home_team),
        away_team=TeamSummary.model_validate(match.away_team),
        venue=VenueSummary.model_validate(match.venue) if match.venue else None,
        home_score=match.home_score,
        away_score=match.away_score,
    )


@router.get("", response_model=list[MatchSummary])
def list_matches(
    status: MatchStatus | None = None, sport: str = "AFL", db: Session = Depends(get_db)
) -> list[MatchSummary]:
    matches = query_matches(db, sport=sport, status=status)
    return [_to_summary(m) for m in matches]


@router.get("/{match_id}", response_model=MatchSummary)
def get_match(match_id: int, db: Session = Depends(get_db)) -> MatchSummary:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return _to_summary(match)
