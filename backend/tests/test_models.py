from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Match, MatchStatus, Round, Season, Sport, Team, Venue


def _make_afl_skeleton(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()

    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL", primary_colour="#000000")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR", primary_colour="#031A2B")
    venue = Venue(name="the MCG", city="Melbourne", state="VIC", timezone="Australia/Melbourne")
    season = Season(sport_id=sport.id, year=2025)
    db_session.add_all([home, away, venue, season])
    db_session.flush()

    round_ = Round(season_id=season.id, round_number=1, name="Round 1")
    db_session.add(round_)
    db_session.flush()

    return sport, home, away, venue, season, round_


def test_create_full_match_graph(db_session):
    sport, home, away, venue, season, round_ = _make_afl_skeleton(db_session)

    match = Match(
        sport_id=sport.id,
        season_id=season.id,
        round_id=round_.id,
        home_team_id=home.id,
        away_team_id=away.id,
        venue_id=venue.id,
        scheduled_start=datetime(2025, 3, 21, 19, 20, tzinfo=timezone.utc),
        status=MatchStatus.SCHEDULED,
    )
    db_session.add(match)
    db_session.commit()

    fetched = db_session.get(Match, match.id)
    assert fetched.home_team.short_name == "COL"
    assert fetched.away_team.short_name == "CAR"
    assert fetched.venue.name == "the MCG"
    assert fetched.round.season.year == 2025
    assert fetched.status == MatchStatus.SCHEDULED


def test_team_name_unique_within_sport(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()

    db_session.add(Team(sport_id=sport.id, name="Collingwood", short_name="COL"))
    db_session.commit()

    db_session.add(Team(sport_id=sport.id, name="Collingwood", short_name="COL2"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_match_cannot_have_same_home_and_away_team(db_session):
    sport, home, _away, venue, season, round_ = _make_afl_skeleton(db_session)

    match = Match(
        sport_id=sport.id,
        season_id=season.id,
        round_id=round_.id,
        home_team_id=home.id,
        away_team_id=home.id,
        venue_id=venue.id,
        scheduled_start=datetime(2025, 3, 21, 19, 20, tzinfo=timezone.utc),
        status=MatchStatus.SCHEDULED,
    )
    db_session.add(match)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_round_number_unique_within_season(db_session):
    sport, *_rest = _make_afl_skeleton(db_session)
    season = db_session.query(Season).one()

    db_session.add(Round(season_id=season.id, round_number=1, name="Duplicate"))
    with pytest.raises(IntegrityError):
        db_session.commit()
