from datetime import date, datetime, timezone

from sqlalchemy import select

from app.ingestion.team_stats import ingest_team_stats
from app.models import Round, Season, Sport, Team, TeamMatchStat
from app.models import Match, MatchStatus
from app.providers.types import TeamStatLine


def _seed(db_session, home="Carlton", away="Richmond", match_date=date(2024, 3, 14)):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2024)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home_team = Team(sport_id=sport.id, name=home, short_name=home[:3].upper())
    away_team = Team(sport_id=sport.id, name=away, short_name=away[:3].upper())
    db_session.add_all([round_, home_team, away_team])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home_team.id, away_team_id=away_team.id,
        scheduled_start=datetime(match_date.year, match_date.month, match_date.day, 19, 30, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=90, away_score=80,
    )
    db_session.add(match)
    db_session.commit()
    return {"sport": sport, "season": season, "match": match, "home": home_team, "away": away_team}


def _row(team, opponent, match_date, **stats) -> TeamStatLine:
    return TeamStatLine(
        match_external_id=f"games/2024/test-{team}-{opponent}.html",
        sport_code="AFL",
        team_name=team,
        recorded_at=datetime.now(timezone.utc),
        stats=stats,
        opponent_name=opponent,
        match_date=match_date,
    )


def test_ingest_creates_stat_row_for_each_team(db_session):
    seed = _seed(db_session)
    rows = [
        _row("Carlton", "Richmond", date(2024, 3, 14), kicks=200, clearances=40),
        _row("Richmond", "Carlton", date(2024, 3, 14), kicks=190, clearances=38),
    ]

    result = ingest_team_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 2
    assert result.unmatched == []
    stats = db_session.scalars(select(TeamMatchStat)).all()
    assert len(stats) == 2
    carlton_stat = next(s for s in stats if s.team_id == seed["home"].id)
    assert carlton_stat.kicks == 200
    assert carlton_stat.clearances == 40
    assert carlton_stat.opponent_team_id == seed["away"].id
    assert carlton_stat.source == "afltables"


def test_ingest_is_idempotent_on_rerun(db_session):
    _seed(db_session)
    rows = [_row("Carlton", "Richmond", date(2024, 3, 14), kicks=200)]

    ingest_team_stats(db_session, rows, season_year=2024)
    result = ingest_team_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 0
    assert result.stats_updated == 0
    assert result.stats_unchanged == 1
    assert len(db_session.scalars(select(TeamMatchStat)).all()) == 1


def test_ingest_updates_changed_values(db_session):
    _seed(db_session)
    ingest_team_stats(db_session, [_row("Carlton", "Richmond", date(2024, 3, 14), kicks=200)], season_year=2024)
    result = ingest_team_stats(db_session, [_row("Carlton", "Richmond", date(2024, 3, 14), kicks=210)], season_year=2024)

    assert result.stats_updated == 1
    stat = db_session.scalar(select(TeamMatchStat))
    assert stat.kicks == 210


def test_date_tolerance_resolves_off_by_one_day(db_session):
    # our Match is on 2024-03-14 (UTC datetime); AFL Tables' reported date is
    # the day before, simulating a local-time vs UTC date-boundary difference
    _seed(db_session, match_date=date(2024, 3, 14))
    rows = [_row("Carlton", "Richmond", date(2024, 3, 13), kicks=200)]

    result = ingest_team_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 1
    assert result.unmatched == []


def test_unresolvable_date_is_reported_not_silently_dropped(db_session):
    _seed(db_session, match_date=date(2024, 3, 14))
    rows = [_row("Carlton", "Richmond", date(2024, 4, 20), kicks=200)]  # far outside tolerance

    result = ingest_team_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 0
    assert len(result.unmatched) == 1
    assert "Carlton" in result.unmatched[0]


def test_unknown_team_is_reported_not_silently_dropped(db_session):
    _seed(db_session)
    rows = [_row("Fitzroy", "Richmond", date(2024, 3, 14), kicks=200)]

    result = ingest_team_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 0
    assert len(result.unmatched) == 1


def test_missing_season_is_reported(db_session):
    _seed(db_session)
    rows = [_row("Carlton", "Richmond", date(2024, 3, 14), kicks=200)]

    result = ingest_team_stats(db_session, rows, season_year=2099)

    assert result.stats_created == 0
    assert len(result.unmatched) == 1


def test_two_meetings_in_one_season_resolved_by_date(db_session):
    """Same two teams play twice a season (home and away legs) — the date
    must disambiguate which meeting a row belongs to."""
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2024)
    db_session.add(season)
    db_session.flush()
    round1 = Round(season_id=season.id, round_number=1)
    round10 = Round(season_id=season.id, round_number=10)
    carlton = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    richmond = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db_session.add_all([round1, round10, carlton, richmond])
    db_session.flush()
    match1 = Match(
        sport_id=sport.id, season_id=season.id, round_id=round1.id,
        home_team_id=carlton.id, away_team_id=richmond.id,
        scheduled_start=datetime(2024, 3, 14, 19, 30, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=90, away_score=80,
    )
    match2 = Match(
        sport_id=sport.id, season_id=season.id, round_id=round10.id,
        home_team_id=richmond.id, away_team_id=carlton.id,
        scheduled_start=datetime(2024, 6, 20, 19, 30, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED, home_score=70, away_score=95,
    )
    db_session.add_all([match1, match2])
    db_session.commit()

    rows = [
        _row("Carlton", "Richmond", date(2024, 3, 14), kicks=200),
        _row("Carlton", "Richmond", date(2024, 6, 20), kicks=250),
    ]
    result = ingest_team_stats(db_session, rows, season_year=2024)

    assert result.stats_created == 2
    stat1 = db_session.scalar(select(TeamMatchStat).where(TeamMatchStat.match_id == match1.id))
    stat2 = db_session.scalar(select(TeamMatchStat).where(TeamMatchStat.match_id == match2.id))
    assert stat1.kicks == 200
    assert stat2.kicks == 250
