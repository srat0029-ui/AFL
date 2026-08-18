"""Tests for the research-only weather-model diagnostic (Current Context
+ Team News Intelligence stage, Section 9) — must honestly report
insufficient data rather than fabricate a historical comparison, and must
never claim sufficiency below the minimum sample."""

from datetime import datetime, timedelta, timezone

from app.models import Match, MatchStatus, Round, Season, Sport, Team, Venue, VenueWeatherSnapshot
from app.player_modelling.weather_diagnostic import MIN_SAMPLE_FOR_HISTORICAL_COMPARISON, weather_model_diagnostic

NOW = datetime.now(timezone.utc)


def _seed_match(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    db.add(round_)
    db.flush()
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([home, away])
    db.flush()
    venue = Venue(name="M.C.G.", latitude=-37.8199, longitude=144.9834)
    db.add(venue)
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        venue_id=venue.id, scheduled_start=NOW + timedelta(days=1), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, venue


def test_no_weather_recorded_reports_unavailable(db_session):
    match, venue = _seed_match(db_session)
    d = weather_model_diagnostic(db_session, match.id, expected_total_points=180.0)
    assert d.weather_available is False
    assert d.has_sufficient_data is False
    assert "no weather forecast" in d.note.lower()


def test_weather_recorded_but_no_historical_sample_is_insufficient(db_session):
    match, venue = _seed_match(db_session)
    db_session.add(
        VenueWeatherSnapshot(
            match_id=match.id, venue_id=venue.id, fetched_at=NOW, forecast_for=match.scheduled_start,
            temperature_c=15.0, rain_probability_pct=80.0, expected_rainfall_mm=5.0, wind_speed_kph=20.0,
            wind_gust_kph=70.0, severe_weather_warning=True, severe_weather_note="High rain probability (80%); High wind gusts forecast (70 km/h)",
        )
    )
    db_session.commit()
    d = weather_model_diagnostic(db_session, match.id, expected_total_points=175.0)
    assert d.weather_available is True
    assert d.is_wet is True
    assert d.is_windy is True
    assert d.projected_total_points == 175.0
    assert d.has_sufficient_data is False
    assert d.historical_sample_similar_condition < MIN_SAMPLE_FOR_HISTORICAL_COMPARISON
    assert "insufficient" in d.note.lower()
