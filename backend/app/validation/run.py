"""Queries the DB and hands rows to app/validation/checks.py. This is the
only place in the validation system that touches a Session — the checks
themselves are pure so they stay testable without a database."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MatchStatus, Player, PlayerMatchStat, Round, Season, Sport, Team, TeamMatchStat, Venue
from app.validation.checks import (
    build_player_stats_coverage,
    build_season_summary,
    build_team_stats_coverage,
    check_matches,
    check_player_match_stats,
    check_player_team_reconciliation,
    check_players,
    check_seasons,
    check_team_match_stats,
    check_teams,
    check_venues,
)
from app.validation.report import Level, ValidationReport


def run_validation(db: Session, sport: str = "AFL") -> ValidationReport:
    report = ValidationReport()

    sport_row = db.scalar(select(Sport).where(Sport.code == sport))
    if sport_row is None:
        report.add(Level.FAIL, "sport", f"No sport row found for code={sport!r} — has ingestion been run?")
        return report

    teams = list(db.scalars(select(Team).where(Team.sport_id == sport_row.id)).all())
    venues = list(db.scalars(select(Venue)).all())
    seasons = list(db.scalars(select(Season).where(Season.sport_id == sport_row.id)).all())
    rounds = list(db.scalars(select(Round).join(Season).where(Season.sport_id == sport_row.id)).all())
    matches = list(db.scalars(select(Match).where(Match.sport_id == sport_row.id)).all())
    team_stats = list(
        db.scalars(select(TeamMatchStat).join(Match, TeamMatchStat.match_id == Match.id).where(Match.sport_id == sport_row.id)).all()
    )
    players = list(db.scalars(select(Player).where(Player.sport_id == sport_row.id)).all())
    player_stats = list(
        db.scalars(
            select(PlayerMatchStat).join(Match, PlayerMatchStat.match_id == Match.id).where(Match.sport_id == sport_row.id)
        ).all()
    )

    check_teams(teams, report)
    check_venues(venues, report)
    check_seasons(seasons, report)
    check_matches(
        matches,
        season_ids={s.id for s in seasons},
        round_ids={r.id for r in rounds},
        team_ids={t.id for t in teams},
        report=report,
    )

    season_year_by_id = {s.id: s.year for s in seasons}
    report.season_summary = build_season_summary(matches, season_year_by_id)

    match_scores: dict[tuple[int, int], tuple[int | None, int | None]] = {}
    completed_match_ids_by_season: dict[int, set[int]] = {}
    for m in matches:
        year = season_year_by_id.get(m.season_id)
        match_scores[(m.id, m.home_team_id)] = (m.home_goals, m.home_behinds)
        match_scores[(m.id, m.away_team_id)] = (m.away_goals, m.away_behinds)
        if m.status == MatchStatus.COMPLETED and year is not None:
            completed_match_ids_by_season.setdefault(year, set()).add(m.id)

    match_id_to_year = {m.id: season_year_by_id.get(m.season_id) for m in matches}
    stat_match_ids_by_season: dict[int, set[int]] = {}
    for s in team_stats:
        year = match_id_to_year.get(s.match_id)
        if year is not None:
            stat_match_ids_by_season.setdefault(year, set()).add(s.match_id)

    check_team_match_stats(team_stats, match_scores, report)
    report.team_stats_coverage = build_team_stats_coverage(completed_match_ids_by_season, stat_match_ids_by_season)

    team_ids = {t.id for t in teams}
    check_players(players, team_ids, report)

    match_team_ids = {m.id: (m.home_team_id, m.away_team_id) for m in matches}
    check_player_match_stats(
        player_stats,
        match_ids={m.id for m in matches},
        player_ids={p.id for p in players},
        team_ids=team_ids,
        match_team_ids=match_team_ids,
        report=report,
    )

    team_stats_by_match_team = {(s.match_id, s.team_id): s for s in team_stats}
    check_player_team_reconciliation(player_stats, team_stats_by_match_team, report)

    player_stat_match_ids_by_season: dict[int, set[int]] = {}
    for s in player_stats:
        year = match_id_to_year.get(s.match_id)
        if year is not None:
            player_stat_match_ids_by_season.setdefault(year, set()).add(s.match_id)
    report.player_stats_coverage = build_player_stats_coverage(completed_match_ids_by_season, player_stat_match_ids_by_season)

    return report
