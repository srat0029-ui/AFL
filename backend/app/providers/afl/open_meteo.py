"""Open-Meteo (https://open-meteo.com) forecast provider — a free,
keyless, structured public JSON weather API aggregating national
meteorological agency data (Current Context + Team News Intelligence
stage, Section 8). Chosen over scraping any weather site: it is a
documented API contract, not HTML, and needs no account/credentials,
matching Section 2's source-preference ordering (structured public
feeds/APIs) without any of the fragility of a scraper.

Verified (2026-08-18) via the provider's own published docs: default
units are already exactly what this app wants — temperature_2m in °C,
wind_speed_10m/wind_gusts_10m in km/h, precipitation in mm,
precipitation_probability in % — so no unit-conversion parameters are
requested. `timezone=UTC` is passed explicitly so the returned hourly
`time` strings are directly comparable to this app's own UTC-stored
`Match.scheduled_start`, rather than relying on Open-Meteo's
location-derived local-time default.

The forecast endpoint only covers a rolling ~16-day window (documented
limit) — a match further out than that genuinely has no real forecast
yet; callers must not fabricate one (see weather_ingestion.py, which
skips those matches and reports why, rather than guessing).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

BASE_URL = "https://api.open-meteo.com/v1/forecast"
PROVIDER_NAME = "open-meteo"
MAX_FORECAST_DAYS = 16

_HOURLY_VARS = "temperature_2m,precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m"


class OpenMeteoError(Exception):
    pass


@dataclass(frozen=True)
class HourlyForecastPoint:
    time: datetime
    temperature_c: float | None
    rain_probability_pct: float | None
    precipitation_mm: float | None
    wind_speed_kph: float | None
    wind_gust_kph: float | None


def _at(series: list | None, i: int) -> float | None:
    if series is None or i >= len(series):
        return None
    v = series[i]
    return float(v) if v is not None else None


class OpenMeteoProvider:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=15.0)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_hourly_forecast(self, *, latitude: float, longitude: float) -> list[HourlyForecastPoint]:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": _HOURLY_VARS,
            "timezone": "UTC",
            "forecast_days": MAX_FORECAST_DAYS,
        }
        try:
            resp = self._client.get(BASE_URL, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenMeteoError(f"Open-Meteo request failed: {exc}") from exc

        data = resp.json()
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        points: list[HourlyForecastPoint] = []
        for i, t in enumerate(times):
            parsed = datetime.fromisoformat(t)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            points.append(
                HourlyForecastPoint(
                    time=parsed,
                    temperature_c=_at(hourly.get("temperature_2m"), i),
                    rain_probability_pct=_at(hourly.get("precipitation_probability"), i),
                    precipitation_mm=_at(hourly.get("precipitation"), i),
                    wind_speed_kph=_at(hourly.get("wind_speed_10m"), i),
                    wind_gust_kph=_at(hourly.get("wind_gusts_10m"), i),
                )
            )
        return points


def nearest_point(points: list[HourlyForecastPoint], target: datetime) -> HourlyForecastPoint | None:
    if not points:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return min(points, key=lambda p: abs((p.time - target).total_seconds()))
