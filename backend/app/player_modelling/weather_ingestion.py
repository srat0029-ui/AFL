"""Fetches and stores venue-local weather forecast snapshots for upcoming
matches (Current Context + Team News Intelligence stage, Section 8) via
Open-Meteo. A match is skipped, with a reported reason, rather than
guessed at, when its venue has no coordinates or its kickoff is outside
Open-Meteo's ~16-day forecast window (see open_meteo.py).

Severe-weather thresholds are fixed, disclosed constants — not a model,
not tuned against outcomes, just a plain-language flag on top of the raw
numbers already shown (Section 8 doesn't ask for judgement here, only
"a severe-weather warning if relevant").
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import VenueWeatherSnapshot
from app.player_modelling.upcoming_features import UpcomingMatchTeams
from app.providers.afl.open_meteo import MAX_FORECAST_DAYS, OpenMeteoError, OpenMeteoProvider, nearest_point

SEVERE_RAIN_PROBABILITY_PCT = 70.0
SEVERE_WIND_GUST_KPH = 60.0


@dataclass
class WeatherIngestionReport:
    matches_considered: int = 0
    snapshots_created: int = 0
    skipped_no_venue: list[int] = field(default_factory=list)
    skipped_no_coordinates: list[int] = field(default_factory=list)
    skipped_too_far_out: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _severe_weather(*, rain_probability_pct: float | None, wind_gust_kph: float | None) -> tuple[bool, str | None]:
    notes = []
    if rain_probability_pct is not None and rain_probability_pct >= SEVERE_RAIN_PROBABILITY_PCT:
        notes.append(f"High rain probability ({rain_probability_pct:.0f}%)")
    if wind_gust_kph is not None and wind_gust_kph >= SEVERE_WIND_GUST_KPH:
        notes.append(f"High wind gusts forecast ({wind_gust_kph:.0f} km/h)")
    if not notes:
        return False, None
    return True, "; ".join(notes)


def refresh_weather_for_matches(
    db: Session, matches: list[UpcomingMatchTeams], *, provider: OpenMeteoProvider | None = None
) -> WeatherIngestionReport:
    from app.models import Venue

    report = WeatherIngestionReport()
    owns_provider = provider is None
    provider = provider or OpenMeteoProvider()
    now = datetime.now(timezone.utc)
    forecast_horizon = now + timedelta(days=MAX_FORECAST_DAYS)

    try:
        for m in matches:
            report.matches_considered += 1

            if m.venue_id is None:
                report.skipped_no_venue.append(m.match_id)
                continue
            venue = db.get(Venue, m.venue_id)
            if venue is None or venue.latitude is None or venue.longitude is None:
                report.skipped_no_coordinates.append(m.match_id)
                continue

            kickoff = m.scheduled_start
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            if kickoff > forecast_horizon:
                report.skipped_too_far_out.append(m.match_id)
                continue

            try:
                points = provider.fetch_hourly_forecast(latitude=venue.latitude, longitude=venue.longitude)
            except OpenMeteoError as exc:
                report.errors.append(f"match {m.match_id}: {exc}")
                continue

            point = nearest_point(points, kickoff)
            if point is None:
                report.errors.append(f"match {m.match_id}: no forecast points returned")
                continue

            severe, severe_note = _severe_weather(rain_probability_pct=point.rain_probability_pct, wind_gust_kph=point.wind_gust_kph)
            snapshot = VenueWeatherSnapshot(
                match_id=m.match_id,
                venue_id=venue.id,
                fetched_at=now,
                forecast_for=point.time,
                temperature_c=point.temperature_c,
                rain_probability_pct=point.rain_probability_pct,
                expected_rainfall_mm=point.precipitation_mm,
                wind_speed_kph=point.wind_speed_kph,
                wind_gust_kph=point.wind_gust_kph,
                severe_weather_warning=severe,
                severe_weather_note=severe_note,
            )
            db.add(snapshot)
            report.snapshots_created += 1

        db.commit()
        return report
    finally:
        if owns_provider:
            provider.close()


def latest_weather_for_match(db: Session, match_id: int) -> VenueWeatherSnapshot | None:
    from sqlalchemy import select

    return db.scalar(
        select(VenueWeatherSnapshot)
        .where(VenueWeatherSnapshot.match_id == match_id)
        .order_by(VenueWeatherSnapshot.fetched_at.desc())
        .limit(1)
    )
