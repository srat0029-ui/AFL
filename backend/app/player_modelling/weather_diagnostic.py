"""Weather-model diagnostic (Current Context + Team News Intelligence
stage, Section 9) — RESEARCH-ONLY: shows the current total-points
projection next to the forecast conditions, and how the Poisson model has
historically performed under similarly wet/windy conditions, IF enough
historical matches carry a weather record to say anything meaningful.

Today that sample is honestly (near) zero: VenueWeatherSnapshot only
starts being populated going forward for upcoming matches from this stage
onward (Section 8) — there is no backfilled weather history for the
~1,400 historical matches already in the database, and this stage
deliberately does not attempt that backfill (out of scope — see the
stage report). This module still does the real query rather than a stub,
so the diagnostic becomes genuinely useful as weather snapshots
accumulate over future rounds, without needing to be rewritten later.

Never feeds into the Poisson model itself (Section 8/15's explicit
instruction) — this is a side-by-side display only.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, PoissonMatchPrediction, VenueWeatherSnapshot

# A historical match counts as "wet"/"windy" for bucketing using the same
# thresholds weather_ingestion.py uses to flag a severe-weather warning —
# one consistent definition of "notable weather" across the stage, not a
# second threshold set invented just for this diagnostic.
from app.player_modelling.weather_ingestion import SEVERE_RAIN_PROBABILITY_PCT, SEVERE_WIND_GUST_KPH

MIN_SAMPLE_FOR_HISTORICAL_COMPARISON = 20


@dataclass(frozen=True)
class WeatherDiagnostic:
    match_id: int
    weather_available: bool
    rain_probability_pct: float | None
    wind_gust_kph: float | None
    is_wet: bool
    is_windy: bool
    projected_total_points: float | None
    historical_sample_overall: int
    historical_mae_overall: float | None
    historical_sample_similar_condition: int
    historical_mae_similar_condition: float | None
    has_sufficient_data: bool
    note: str


def _historical_rows_with_weather(db: Session) -> list[tuple[PoissonMatchPrediction, VenueWeatherSnapshot]]:
    rows = db.execute(
        select(PoissonMatchPrediction, VenueWeatherSnapshot)
        .join(VenueWeatherSnapshot, VenueWeatherSnapshot.match_id == PoissonMatchPrediction.match_id)
    ).all()
    return [(p, w) for p, w in rows]


def _mae(rows: list[PoissonMatchPrediction]) -> float | None:
    if not rows:
        return None
    return sum(abs(p.expected_total_points - p.actual_total_points) for p in rows) / len(rows)


def weather_model_diagnostic(db: Session, match_id: int, *, expected_total_points: float | None) -> WeatherDiagnostic:
    from app.player_modelling.weather_ingestion import latest_weather_for_match

    weather = latest_weather_for_match(db, match_id)
    if weather is None:
        return WeatherDiagnostic(
            match_id=match_id, weather_available=False, rain_probability_pct=None, wind_gust_kph=None,
            is_wet=False, is_windy=False, projected_total_points=expected_total_points,
            historical_sample_overall=0, historical_mae_overall=None,
            historical_sample_similar_condition=0, historical_mae_similar_condition=None,
            has_sufficient_data=False, note="No weather forecast recorded for this match yet.",
        )

    is_wet = weather.rain_probability_pct is not None and weather.rain_probability_pct >= SEVERE_RAIN_PROBABILITY_PCT
    is_windy = weather.wind_gust_kph is not None and weather.wind_gust_kph >= SEVERE_WIND_GUST_KPH

    historical = _historical_rows_with_weather(db)
    overall_preds = [p for p, _w in historical]
    similar_preds = [
        p
        for p, w in historical
        if (bool(w.rain_probability_pct is not None and w.rain_probability_pct >= SEVERE_RAIN_PROBABILITY_PCT) == is_wet)
        and (bool(w.wind_gust_kph is not None and w.wind_gust_kph >= SEVERE_WIND_GUST_KPH) == is_windy)
    ]

    has_sufficient = len(similar_preds) >= MIN_SAMPLE_FOR_HISTORICAL_COMPARISON
    if not historical:
        note = (
            "Insufficient historical weather data: no completed matches in the database currently carry a "
            "recorded weather snapshot, so historical model performance under similar conditions cannot be shown yet."
        )
    elif not has_sufficient:
        note = (
            f"Insufficient historical weather data for a reliable comparison under similar conditions "
            f"(only {len(similar_preds)} historical match(es) match this condition, minimum {MIN_SAMPLE_FOR_HISTORICAL_COMPARISON})."
        )
    else:
        note = f"Based on {len(similar_preds)} historical match(es) under similar conditions."

    return WeatherDiagnostic(
        match_id=match_id, weather_available=True,
        rain_probability_pct=weather.rain_probability_pct, wind_gust_kph=weather.wind_gust_kph,
        is_wet=is_wet, is_windy=is_windy, projected_total_points=expected_total_points,
        historical_sample_overall=len(overall_preds), historical_mae_overall=_mae(overall_preds),
        historical_sample_similar_condition=len(similar_preds), historical_mae_similar_condition=_mae(similar_preds),
        has_sufficient_data=has_sufficient, note=note,
    )


def weather_diagnostic_as_dict(d: WeatherDiagnostic) -> dict:
    return {
        "match_id": d.match_id,
        "weather_available": d.weather_available,
        "rain_probability_pct": d.rain_probability_pct,
        "wind_gust_kph": d.wind_gust_kph,
        "is_wet": d.is_wet,
        "is_windy": d.is_windy,
        "projected_total_points": d.projected_total_points,
        "historical_sample_overall": d.historical_sample_overall,
        "historical_mae_overall": d.historical_mae_overall,
        "historical_sample_similar_condition": d.historical_sample_similar_condition,
        "historical_mae_similar_condition": d.historical_mae_similar_condition,
        "has_sufficient_data": d.has_sufficient_data,
        "note": d.note,
    }
