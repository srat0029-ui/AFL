"""One-time data-enrichment script: populates Venue.latitude/longitude with
real, publicly known stadium coordinates (Current Context + Team News
Intelligence stage, Section 8) — the app never previously needed
geographic coordinates, so every existing Venue row has NULL lat/lon.
Weather forecasting (weather_ingestion.py) needs them to call Open-Meteo.

Coordinates are ordinary public geographic facts about stadium locations
(same category of fact as a venue's city/state, already stored), not
scraped from any single copyrighted source. Two rarely-used community
grounds (Adelaide Hills, Barossa Park) only have an approximate
regional-town coordinate — good enough for a regional weather forecast,
called out explicitly rather than left silently wrong.

Run once: `python scripts/seed_venue_coordinates.py`. Idempotent — only
fills rows that are currently NULL, never overwrites a value someone set
by hand later.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Venue

# (latitude, longitude, city, state) - name must match Venue.name exactly.
VENUE_COORDINATES: dict[str, tuple[float, float, str, str]] = {
    "M.C.G.": (-37.8199, 144.9834, "Melbourne", "VIC"),
    "Carrara": (-28.0058, 153.3694, "Gold Coast", "QLD"),
    "Docklands": (-37.8166, 144.9475, "Melbourne", "VIC"),
    "S.C.G.": (-33.8916, 151.2247, "Sydney", "NSW"),
    "Adelaide Oval": (-34.9156, 138.5960, "Adelaide", "SA"),
    "Subiaco": (-31.9483, 115.8137, "Perth", "WA"),
    "Gabba": (-27.4858, 153.0381, "Brisbane", "QLD"),
    "Manuka Oval": (-35.3181, 149.1435, "Canberra", "ACT"),
    "Bellerive Oval": (-42.8768, 147.3669, "Hobart", "TAS"),
    "Kardinia Park": (-38.1580, 144.3548, "Geelong", "VIC"),
    "York Park": (-41.4275, 147.1472, "Launceston", "TAS"),
    "Sydney Showground": (-33.8474, 151.0634, "Sydney", "NSW"),
    "Traeger Park": (-23.7009, 133.8807, "Alice Springs", "NT"),
    "Marrara Oval": (-12.3987, 130.8823, "Darwin", "NT"),
    "Cazaly's Stadium": (-16.9235, 145.7276, "Cairns", "QLD"),
    "Stadium Australia": (-33.8470, 151.0634, "Sydney", "NSW"),
    "UNSW Canberra Oval": (-35.3167, 149.1667, "Canberra", "ACT"),
    "University of Tasmania Stadium": (-41.4275, 147.1472, "Launceston", "TAS"),
    "Adelaide Arena at Jiangwan Stadium": (31.3204, 121.5015, "Shanghai", None),
    "GMHBA Stadium": (-38.1580, 144.3548, "Geelong", "VIC"),
    "Mars Stadium": (-37.5622, 143.8503, "Ballarat", "VIC"),
    "Optus Stadium": (-31.9513, 115.8891, "Perth", "WA"),
    "Perth Stadium": (-31.9513, 115.8891, "Perth", "WA"),
    "Marvel Stadium": (-37.8166, 144.9475, "Melbourne", "VIC"),
    "Eureka Stadium": (-37.5622, 143.8503, "Ballarat", "VIC"),
    "Jiangwan Stadium": (31.3204, 121.5015, "Shanghai", None),
    "Riverway Stadium": (-19.2848, 146.7539, "Townsville", "QLD"),
    "Norwood Oval": (-34.9142, 138.6323, "Adelaide", "SA"),
    # Approximate regional-town coordinate only - see module docstring.
    "Adelaide Hills": (-34.9285, 138.7458, "Adelaide Hills", "SA"),
    "Barossa Park": (-34.4708, 138.9955, "Nuriootpa", "SA"),
    "Hands Oval": (-33.3272, 115.6359, "Bunbury", "WA"),
}


def main() -> None:
    db = SessionLocal()
    try:
        venues = db.query(Venue).all()
        updated, skipped_existing, unmatched = [], [], []
        for v in venues:
            if v.latitude is not None and v.longitude is not None:
                skipped_existing.append(v.name)
                continue
            coords = VENUE_COORDINATES.get(v.name)
            if coords is None:
                unmatched.append(v.name)
                continue
            lat, lon, city, state = coords
            v.latitude = lat
            v.longitude = lon
            if v.city is None:
                v.city = city
            if v.state is None:
                v.state = state
            updated.append(v.name)
        db.commit()
        print(f"Updated {len(updated)} venue(s): {updated}")
        if skipped_existing:
            print(f"Already had coordinates, left unchanged: {skipped_existing}")
        if unmatched:
            print(f"No known coordinates for: {unmatched} - add to VENUE_COORDINATES if these need weather support.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
