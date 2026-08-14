"""CLI entrypoint for ingesting AFL fixtures/results from Squiggle.

Usage:
    python -m app.ingestion.cli --seasons 2016 2025
    python -m app.ingestion.cli --upcoming
    python -m app.ingestion.cli --seasons 2016 2025 --upcoming
"""

import argparse
import sys

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ingestion.fixtures import ingest_fixtures
from app.providers.afl.squiggle import SquiggleFixtureProvider


def backfill_seasons(db: Session, provider: SquiggleFixtureProvider, start_year: int, end_year: int) -> list[int]:
    """Ingests each season in [start_year, end_year]. Returns the years that
    failed (e.g. Squiggle flakiness surviving the provider's own retries) so
    the caller can report them and the user can re-run just those years,
    rather than one bad season aborting the whole backfill."""
    failed_years: list[int] = []
    for year in range(start_year, end_year + 1):
        print(f"Fetching AFL {year} season from Squiggle...")
        try:
            fixtures = provider.get_fixtures("AFL", year)
        except Exception as exc:  # noqa: BLE001 — deliberately broad: log and move to the next season
            print(f"  {year}: FAILED ({exc})")
            failed_years.append(year)
            continue
        result = ingest_fixtures(db, fixtures)
        print(
            f"  {year}: {len(fixtures)} fixtures seen | "
            f"created={result.matches_created} updated={result.matches_updated} "
            f"unchanged={result.matches_unchanged}"
        )
    return failed_years


def ingest_upcoming(db: Session, provider: SquiggleFixtureProvider) -> None:
    print("Fetching upcoming AFL fixtures from Squiggle...")
    fixtures = provider.get_upcoming_fixtures("AFL")
    result = ingest_fixtures(db, fixtures)
    print(
        f"  {len(fixtures)} upcoming fixtures seen | "
        f"created={result.matches_created} updated={result.matches_updated} "
        f"unchanged={result.matches_unchanged}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest AFL fixtures/results from Squiggle.")
    parser.add_argument(
        "--seasons",
        nargs=2,
        type=int,
        metavar=("START_YEAR", "END_YEAR"),
        help="Backfill historical seasons, inclusive, e.g. --seasons 2016 2025",
    )
    parser.add_argument(
        "--upcoming", action="store_true", help="Ingest not-yet-played fixtures for the current season"
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.5,
        help="Seconds to wait between Squiggle requests (default 0.5 — be a good citizen of a free hobby API)",
    )
    args = parser.parse_args(argv)

    if not args.seasons and not args.upcoming:
        parser.print_help()
        return 1

    if args.seasons and args.seasons[0] > args.seasons[1]:
        parser.error("START_YEAR must be <= END_YEAR")

    provider = SquiggleFixtureProvider(request_delay_seconds=args.request_delay)
    db = SessionLocal()
    failed_years: list[int] = []
    try:
        if args.seasons:
            failed_years = backfill_seasons(db, provider, args.seasons[0], args.seasons[1])
        if args.upcoming:
            ingest_upcoming(db, provider)
    finally:
        db.close()

    if failed_years:
        print(f"\nSeasons that failed and can be retried: {failed_years}")
        print(f"  e.g. python -m app.ingestion.cli --seasons {min(failed_years)} {max(failed_years)}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
