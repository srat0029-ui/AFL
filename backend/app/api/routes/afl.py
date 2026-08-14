"""AFL-specific data endpoints, independent of modelling: teams, seasons,
and match listings with composable filters. Reuses the same query/summary
logic as the legacy /api/matches routes (see app/api/match_queries.py) —
this router exists to give consumers a clean, discoverable surface without
needing predictions/edges to exist first.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.match_queries import query_matches
from app.api.routes.matches import _to_summary
from app.api.schemas import MatchSummary, SeasonSummary, TeamSummary
from app.database import get_db
from app.models import Match, MatchStatus, Season, Sport, Team

router = APIRouter(prefix="/api/afl", tags=["afl"])


@router.get("/teams", response_model=list[TeamSummary])
def list_teams(sport: str = "AFL", db: Session = Depends(get_db)) -> list[TeamSummary]:
    teams = db.scalars(select(Team).join(Sport).where(Sport.code == sport).order_by(Team.name)).all()
    return [TeamSummary.model_validate(t) for t in teams]


@router.get("/seasons", response_model=list[SeasonSummary])
def list_seasons(sport: str = "AFL", db: Session = Depends(get_db)) -> list[SeasonSummary]:
    seasons = db.scalars(select(Season).join(Sport).where(Sport.code == sport).order_by(Season.year)).all()
    return [SeasonSummary.model_validate(s) for s in seasons]


@router.get("/matches", response_model=list[MatchSummary])
def list_afl_matches(
    season: int | None = None,
    round_number: int | None = None,
    team_id: int | None = None,
    status: MatchStatus | None = None,
    order: str = "asc",
    limit: int | None = None,
    sport: str = "AFL",
    db: Session = Depends(get_db),
) -> list[MatchSummary]:
    matches = query_matches(
        db,
        sport=sport,
        status=status,
        season_year=season,
        round_number=round_number,
        team_id=team_id,
        order=order,
        limit=limit,
    )
    return [_to_summary(m) for m in matches]


@router.get("/matches/upcoming", response_model=list[MatchSummary])
def list_upcoming_matches(sport: str = "AFL", db: Session = Depends(get_db)) -> list[MatchSummary]:
    matches = query_matches(db, sport=sport, status=MatchStatus.SCHEDULED, order="asc")
    return [_to_summary(m) for m in matches]


@router.get("/matches/{match_id}", response_model=MatchSummary)
def get_afl_match(match_id: int, db: Session = Depends(get_db)) -> MatchSummary:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return _to_summary(match)


@router.get("/seasons/{year}/matches", response_model=list[MatchSummary])
def list_season_matches(
    year: int,
    round_number: int | None = None,
    team_id: int | None = None,
    status: MatchStatus | None = None,
    order: str = "asc",
    sport: str = "AFL",
    db: Session = Depends(get_db),
) -> list[MatchSummary]:
    matches = query_matches(
        db,
        sport=sport,
        status=status,
        season_year=year,
        round_number=round_number,
        team_id=team_id,
        order=order,
    )
    return [_to_summary(m) for m in matches]
