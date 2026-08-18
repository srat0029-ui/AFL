"""Timestamped pre-match weather forecast snapshots (Current Context +
Team News Intelligence stage, Section 8) — sourced from Open-Meteo (see
app/providers/afl/open_meteo.py), a free, keyless, structured public
weather API, using the venue's own latitude/longitude already stored on
Venue. Append-only: every fetch writes a NEW row rather than overwriting
the previous forecast, so a forecast issued Monday and a revised forecast
issued Thursday are both visible — the same "preserve history, latest
wins for current state" convention as MatchContextItem.

Deliberately NOT fed into the Poisson/player models (Section 8's explicit
instruction) — this table exists purely for display and the research-only
diagnostic in weather_diagnostic.py.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class VenueWeatherSnapshot(TimestampMixin, Base):
    __tablename__ = "venue_weather_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), nullable=False, index=True)

    # When this forecast was fetched vs. the match datetime it forecasts for
    # ("venue-local forecast", Section 8) — kept distinct since a forecast
    # fetched days out and one fetched hours out for the SAME kickoff are
    # both legitimately stored, and a caller wants "latest fetched" for
    # display but may want "forecast_for" to confirm it's for the right game.
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    forecast_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    rain_probability_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_rainfall_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_gust_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    severe_weather_warning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Free-text detail behind severe_weather_warning (e.g. "High wind gusts
    # forecast (62 km/h)") — never invented, always derived directly from
    # the numeric fields above by weather_ingestion.py's own fixed thresholds.
    severe_weather_note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False, default="open-meteo")

    match: Mapped["Match"] = relationship(foreign_keys=[match_id])
    venue: Mapped["Venue"] = relationship(foreign_keys=[venue_id])

    def __repr__(self) -> str:
        return f"<VenueWeatherSnapshot match={self.match_id} fetched_at={self.fetched_at}>"
