"""AFL-specific data endpoints, independent of modelling: teams, seasons,
and match listings with composable filters. Reuses the same query/summary
logic as the legacy /api/matches routes (see app/api/match_queries.py) —
this router exists to give consumers a clean, discoverable surface without
needing predictions/edges to exist first.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.match_queries import query_matches
from app.api.player_queries import PlayerGameRow, build_player_form, get_match_players, get_player_games, query_players
from app.api.routes.matches import _to_summary
from app.api.schemas import (
    MatchPlayersRead,
    MatchSummary,
    PlayerFormRead,
    PlayerGameStatRead,
    PlayerGamesRead,
    PlayerListRead,
    PlayerSummaryRead,
    SeasonAverageRead,
    SeasonSummary,
    TeamSummary,
)
from app.database import get_db
from app.models import Match, MatchStatus, Player, Season, Sport, Team

router = APIRouter(prefix="/api/afl", tags=["afl"])

_MAX_PAGE_SIZE = 200


def _game_stat_read(row: PlayerGameRow) -> PlayerGameStatRead:
    stat = row.stat
    return PlayerGameStatRead(
        match_id=row.match.id,
        player_id=stat.player_id,
        player_display_name=stat.player.display_name,
        season_year=row.season_year,
        round_number=row.round_number,
        scheduled_start=row.match.scheduled_start,
        team=TeamSummary.model_validate(stat.team),
        opponent_team=TeamSummary.model_validate(stat.opponent_team) if stat.opponent_team else None,
        jumper_number=stat.jumper_number,
        subbed_on=stat.subbed_on,
        subbed_off=stat.subbed_off,
        kicks=stat.kicks, marks=stat.marks, handballs=stat.handballs, disposals=stat.disposals,
        goals=stat.goals, behinds=stat.behinds, hitouts=stat.hitouts, tackles=stat.tackles,
        rebound_50s=stat.rebound_50s, inside_50s=stat.inside_50s, clearances=stat.clearances,
        clangers=stat.clangers, frees_for=stat.frees_for, frees_against=stat.frees_against,
        brownlow_votes=stat.brownlow_votes, contested_possessions=stat.contested_possessions,
        uncontested_possessions=stat.uncontested_possessions, contested_marks=stat.contested_marks,
        marks_inside_50=stat.marks_inside_50, one_percenters=stat.one_percenters, bounces=stat.bounces,
        goal_assists=stat.goal_assists, time_on_ground_pct=stat.time_on_ground_pct, fantasy_points=stat.fantasy_points,
    )


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


@router.get("/players", response_model=PlayerListRead)
def list_players(
    team_id: int | None = None,
    season: int | None = None,
    is_active: bool | None = None,
    name: str | None = Query(default=None, description="Case-insensitive substring search on display name"),
    limit: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    sport: str = "AFL",
    db: Session = Depends(get_db),
) -> PlayerListRead:
    players, total = query_players(
        db, sport=sport, team_id=team_id, season_year=season, is_active=is_active,
        name_search=name, limit=limit, offset=offset,
    )
    return PlayerListRead(
        players=[PlayerSummaryRead.model_validate(p) for p in players], total=total, limit=limit, offset=offset,
    )


@router.get("/players/{player_id}", response_model=PlayerSummaryRead)
def get_player(player_id: int, db: Session = Depends(get_db)) -> PlayerSummaryRead:
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return PlayerSummaryRead.model_validate(player)


@router.get("/players/{player_id}/games", response_model=PlayerGamesRead)
def get_player_game_log(
    player_id: int,
    season: int | None = None,
    limit: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> PlayerGamesRead:
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    rows, total = get_player_games(db, player_id, season_year=season, limit=limit, offset=offset)
    return PlayerGamesRead(
        player=PlayerSummaryRead.model_validate(player),
        games=[_game_stat_read(r) for r in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get("/players/{player_id}/form", response_model=PlayerFormRead)
def get_player_form(
    player_id: int,
    recent_games: int = Query(default=10, ge=1, le=_MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
) -> PlayerFormRead:
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    recent, season_averages = build_player_form(db, player_id, recent_n=recent_games)
    return PlayerFormRead(
        player=PlayerSummaryRead.model_validate(player),
        recent_games=[_game_stat_read(r) for r in recent],
        season_averages=[SeasonAverageRead(season_year=s.season_year, games_played=s.games_played, averages=s.averages) for s in season_averages],
    )


@router.get("/matches/{match_id}/players", response_model=MatchPlayersRead)
def get_match_player_stats(match_id: int, db: Session = Depends(get_db)) -> MatchPlayersRead:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = get_match_players(db, match_id)
    home_rows = [r for r in rows if r.stat.team_id == match.home_team_id]
    away_rows = [r for r in rows if r.stat.team_id == match.away_team_id]
    return MatchPlayersRead(
        match_id=match_id,
        home_team_players=[_game_stat_read(r) for r in home_rows],
        away_team_players=[_game_stat_read(r) for r in away_rows],
    )
