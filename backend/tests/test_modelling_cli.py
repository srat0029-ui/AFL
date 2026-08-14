from datetime import datetime, timezone

from app.modelling.cli import load_completed_matches
from app.models import Match, MatchStatus, Round, Season, Sport, Team


def _seed(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()

    season = Season(sport_id=sport.id, year=2024)
    db_session.add(season)
    db_session.flush()

    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Home", short_name="HOM")
    away = Team(sport_id=sport.id, name="Away", short_name="AWY")
    db_session.add_all([round_, home, away])
    db_session.flush()

    completed = Match(
        sport_id=sport.id,
        season_id=season.id,
        round_id=round_.id,
        home_team_id=home.id,
        away_team_id=away.id,
        scheduled_start=datetime(2024, 3, 1, tzinfo=timezone.utc),
        status=MatchStatus.COMPLETED,
        home_score=80,
        away_score=70,
    )
    scheduled = Match(
        sport_id=sport.id,
        season_id=season.id,
        round_id=round_.id,
        home_team_id=away.id,
        away_team_id=home.id,
        scheduled_start=datetime(2024, 3, 8, tzinfo=timezone.utc),
        status=MatchStatus.SCHEDULED,
    )
    db_session.add_all([completed, scheduled])
    db_session.commit()
    return completed, scheduled


def test_load_completed_matches_excludes_scheduled_games(db_session):
    completed, _scheduled = _seed(db_session)

    results = load_completed_matches(db_session)

    assert len(results) == 1
    assert results[0].match_id == completed.id
    assert results[0].home_score == 80
    assert results[0].away_score == 70
    assert results[0].season_year == 2024


def test_load_completed_matches_empty_db_returns_empty_list(db_session):
    assert load_completed_matches(db_session) == []
