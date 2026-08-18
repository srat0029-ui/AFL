"""Tests for weather ingestion (Current Context + Team News Intelligence
stage, Section 8) — venue-coordinate handling, the 16-day forecast
window, severe-weather thresholds, and timestamp handling. All HTTP calls
are mocked (httpx.MockTransport) — never a real network call in an
automated test."""

from datetime import datetime, timedelta, timezone

import httpx

from app.models import Match, MatchStatus, Round, Season, Sport, Team, Venue, VenueWeatherSnapshot
from app.player_modelling.upcoming_features import UpcomingMatchTeams
from app.player_modelling.weather_ingestion import SEVERE_RAIN_PROBABILITY_PCT, SEVERE_WIND_GUST_KPH, latest_weather_for_match, refresh_weather_for_matches
from app.providers.afl.open_meteo import OpenMeteoProvider

NOW = datetime.now(timezone.utc)


def _client_with_handler(handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(base_url="https://api.open-meteo.com", transport=transport)


def _forecast_response(hours: list[datetime], *, temp=15.0, rain_prob=10.0, precip=0.0, wind=10.0, gust=20.0):
    times = [h.strftime("%Y-%m-%dT%H:%M") for h in hours]
    return httpx.Response(
        200,
        json={
            "hourly": {
                "time": times,
                "temperature_2m": [temp] * len(hours),
                "precipitation_probability": [rain_prob] * len(hours),
                "precipitation": [precip] * len(hours),
                "wind_speed_10m": [wind] * len(hours),
                "wind_gusts_10m": [gust] * len(hours),
            }
        },
    )


def _seed_match(db, *, with_venue=True, with_coords=True):
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

    venue_id = None
    if with_venue:
        venue = Venue(name="M.C.G.", latitude=-37.8199 if with_coords else None, longitude=144.9834 if with_coords else None)
        db.add(venue)
        db.flush()
        venue_id = venue.id

    kickoff = NOW + timedelta(days=1)
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        venue_id=venue_id, scheduled_start=kickoff, status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, kickoff


def _upcoming(match, kickoff) -> UpcomingMatchTeams:
    return UpcomingMatchTeams(
        match_id=match.id, home_team_id=match.home_team_id, away_team_id=match.away_team_id, venue_id=match.venue_id,
        scheduled_start=kickoff, season_year=2026, round_number=1, is_final=False,
    )


def test_creates_snapshot_for_match_with_coordinates(db_session):
    match, kickoff = _seed_match(db_session)
    hours = [kickoff + timedelta(hours=h) for h in range(-2, 3)]
    provider = OpenMeteoProvider(client=_client_with_handler(lambda r: _forecast_response(hours)))

    report = refresh_weather_for_matches(db_session, [_upcoming(match, kickoff)], provider=provider)
    assert report.snapshots_created == 1
    assert report.skipped_no_venue == []
    assert report.skipped_no_coordinates == []

    snapshot = latest_weather_for_match(db_session, match.id)
    assert snapshot is not None
    assert snapshot.temperature_c == 15.0
    assert snapshot.source == "open-meteo"


def test_skips_match_with_no_venue(db_session):
    match, kickoff = _seed_match(db_session, with_venue=False)
    provider = OpenMeteoProvider(client=_client_with_handler(lambda r: _forecast_response([kickoff])))
    report = refresh_weather_for_matches(db_session, [_upcoming(match, kickoff)], provider=provider)
    assert report.snapshots_created == 0
    assert report.skipped_no_venue == [match.id]


def test_skips_match_with_venue_missing_coordinates(db_session):
    match, kickoff = _seed_match(db_session, with_coords=False)
    provider = OpenMeteoProvider(client=_client_with_handler(lambda r: _forecast_response([kickoff])))
    report = refresh_weather_for_matches(db_session, [_upcoming(match, kickoff)], provider=provider)
    assert report.snapshots_created == 0
    assert report.skipped_no_coordinates == [match.id]


def test_skips_match_beyond_forecast_window(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    db_session.add(round_)
    db_session.flush()
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db_session.add_all([home, away])
    db_session.flush()
    venue = Venue(name="M.C.G.", latitude=-37.8199, longitude=144.9834)
    db_session.add(venue)
    db_session.flush()
    far_kickoff = NOW + timedelta(days=30)
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        venue_id=venue.id, scheduled_start=far_kickoff, status=MatchStatus.SCHEDULED,
    )
    db_session.add(match)
    db_session.commit()

    provider = OpenMeteoProvider(client=_client_with_handler(lambda r: _forecast_response([NOW])))
    report = refresh_weather_for_matches(db_session, [_upcoming(match, far_kickoff)], provider=provider)
    assert report.snapshots_created == 0
    assert report.skipped_too_far_out == [match.id]


def test_severe_rain_probability_flags_warning(db_session):
    match, kickoff = _seed_match(db_session)
    hours = [kickoff]
    provider = OpenMeteoProvider(client=_client_with_handler(lambda r: _forecast_response(hours, rain_prob=SEVERE_RAIN_PROBABILITY_PCT + 5, gust=10.0)))
    refresh_weather_for_matches(db_session, [_upcoming(match, kickoff)], provider=provider)
    snapshot = latest_weather_for_match(db_session, match.id)
    assert snapshot.severe_weather_warning is True
    assert "rain" in snapshot.severe_weather_note.lower()


def test_severe_wind_gust_flags_warning(db_session):
    match, kickoff = _seed_match(db_session)
    hours = [kickoff]
    provider = OpenMeteoProvider(client=_client_with_handler(lambda r: _forecast_response(hours, rain_prob=0.0, gust=SEVERE_WIND_GUST_KPH + 10)))
    refresh_weather_for_matches(db_session, [_upcoming(match, kickoff)], provider=provider)
    snapshot = latest_weather_for_match(db_session, match.id)
    assert snapshot.severe_weather_warning is True
    assert "wind" in snapshot.severe_weather_note.lower()


def test_mild_weather_no_warning(db_session):
    match, kickoff = _seed_match(db_session)
    hours = [kickoff]
    provider = OpenMeteoProvider(client=_client_with_handler(lambda r: _forecast_response(hours, rain_prob=5.0, gust=15.0)))
    refresh_weather_for_matches(db_session, [_upcoming(match, kickoff)], provider=provider)
    snapshot = latest_weather_for_match(db_session, match.id)
    assert snapshot.severe_weather_warning is False
    assert snapshot.severe_weather_note is None


def test_refresh_is_append_only_not_overwrite(db_session):
    """Section 8: 'store pre-match forecasts with timestamped snapshots' -
    a second refresh must add a NEW row, not overwrite the first."""
    match, kickoff = _seed_match(db_session)
    hours = [kickoff]
    provider = OpenMeteoProvider(client=_client_with_handler(lambda r: _forecast_response(hours)))
    refresh_weather_for_matches(db_session, [_upcoming(match, kickoff)], provider=provider)
    refresh_weather_for_matches(db_session, [_upcoming(match, kickoff)], provider=provider)
    count = db_session.query(VenueWeatherSnapshot).filter(VenueWeatherSnapshot.match_id == match.id).count()
    assert count == 2
    # latest_weather_for_match still returns exactly one - the most recent.
    assert latest_weather_for_match(db_session, match.id) is not None
