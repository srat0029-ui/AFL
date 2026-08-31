"""Shared player-listing/game query logic for the /api/afl/players* routes.
Mirrors app/api/match_queries.py's separation: read/query shaping lives in
the API layer, not the ingestion or modelling pipelines.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Match, Player, PlayerMatchStat, Round, Season, Sport
from app.player_modelling.current_players import current_player_ids


def query_players(
    db: Session,
    *,
    sport: str = "AFL",
    team_id: int | None = None,
    season_year: int | None = None,
    is_active: bool | None = None,
    name_search: str | None = None,
    current_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Player], int]:
    """`current_only` (data-scoping fix, product-quality stage): restricts
    results to the currently active/relevant player population (see
    current_players.py) — for current-facing player search/dropdowns, so a
    long-retired player never shows up. Independent of `season_year`, which
    is a plain "played in this specific season" filter used by historical
    research call sites and stays exactly as before."""
    query = select(Player).join(Sport, Player.sport_id == Sport.id).where(Sport.code == sport)

    if team_id is not None:
        query = query.where(Player.current_team_id == team_id)
    if is_active is not None:
        query = query.where(Player.is_active == is_active)
    if name_search:
        query = query.where(Player.display_name.ilike(f"%{name_search}%"))
    if season_year is not None:
        played_in_season = (
            select(PlayerMatchStat.player_id)
            .join(Match, PlayerMatchStat.match_id == Match.id)
            .join(Season, Match.season_id == Season.id)
            .where(Season.year == season_year)
        )
        query = query.where(Player.id.in_(played_in_season))
    if current_only:
        query = query.where(Player.id.in_(current_player_ids(db, sport=sport)))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    query = query.order_by(Player.display_name).offset(offset).limit(limit)
    return list(db.scalars(query).all()), total


@dataclass(frozen=True)
class PlayerGameRow:
    """A PlayerMatchStat plus the match context (season/round/scheduled
    time) it doesn't itself store — those live on Match/Round/Season."""

    stat: PlayerMatchStat
    match: Match
    season_year: int
    round_number: int


def get_player_games(
    db: Session, player_id: int, *, season_year: int | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[PlayerGameRow], int]:
    query = (
        select(PlayerMatchStat, Match, Season.year, Round.round_number)
        .join(Match, PlayerMatchStat.match_id == Match.id)
        .join(Season, Match.season_id == Season.id)
        .join(Round, Match.round_id == Round.id)
        .where(PlayerMatchStat.player_id == player_id)
    )
    if season_year is not None:
        query = query.where(Season.year == season_year)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    query = query.order_by(Match.scheduled_start.desc()).offset(offset).limit(limit)
    rows = db.execute(query).all()
    return [PlayerGameRow(stat=r[0], match=r[1], season_year=r[2], round_number=r[3]) for r in rows], total


_AVERAGEABLE_FIELDS = [
    "kicks", "marks", "handballs", "disposals", "goals", "behinds", "hitouts", "tackles",
    "rebound_50s", "inside_50s", "clearances", "clangers", "frees_for", "frees_against",
    "contested_possessions", "uncontested_possessions", "contested_marks", "marks_inside_50",
    "one_percenters", "bounces", "goal_assists", "time_on_ground_pct",
]


@dataclass(frozen=True)
class SeasonAverage:
    season_year: int
    games_played: int
    averages: dict[str, float]  # field_name -> mean over games where that field was present


def _season_average(rows: list[PlayerGameRow], season_year: int) -> SeasonAverage:
    season_rows = [r for r in rows if r.season_year == season_year]
    averages = {}
    for field_name in _AVERAGEABLE_FIELDS:
        values = [v for r in season_rows if (v := getattr(r.stat, field_name)) is not None]
        if values:
            averages[field_name] = sum(values) / len(values)
    return SeasonAverage(season_year=season_year, games_played=len(season_rows), averages=averages)


def build_player_form(db: Session, player_id: int, recent_n: int = 10) -> tuple[list[PlayerGameRow], list[SeasonAverage]]:
    """Returns (recent_n most recent games, one SeasonAverage per season the
    player has a recorded game in) — the whole career's worth of rows is
    fetched (bounded: a career is at most a few hundred games), no
    pagination needed for this aggregate view."""
    query = (
        select(PlayerMatchStat, Match, Season.year, Round.round_number)
        .join(Match, PlayerMatchStat.match_id == Match.id)
        .join(Season, Match.season_id == Season.id)
        .join(Round, Match.round_id == Round.id)
        .where(PlayerMatchStat.player_id == player_id)
        .order_by(Match.scheduled_start.desc())
    )
    rows = db.execute(query).all()
    all_games = [PlayerGameRow(stat=r[0], match=r[1], season_year=r[2], round_number=r[3]) for r in rows]

    season_years = sorted({g.season_year for g in all_games}, reverse=True)
    season_averages = [_season_average(all_games, year) for year in season_years]
    return all_games[:recent_n], season_averages


def get_match_players(db: Session, match_id: int) -> list[PlayerGameRow]:
    query = (
        select(PlayerMatchStat, Match, Season.year, Round.round_number)
        .join(Match, PlayerMatchStat.match_id == Match.id)
        .join(Season, Match.season_id == Season.id)
        .join(Round, Match.round_id == Round.id)
        .where(PlayerMatchStat.match_id == match_id)
        .order_by(PlayerMatchStat.team_id, PlayerMatchStat.disposals.desc().nullslast())
    )
    rows = db.execute(query).all()
    return [PlayerGameRow(stat=r[0], match=r[1], season_year=r[2], round_number=r[3]) for r in rows]
