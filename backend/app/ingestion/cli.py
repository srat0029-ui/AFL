"""CLI entrypoint for ingesting AFL fixtures/results from Squiggle, advanced
team statistics from AFL Tables, and for validating the resulting data.

Usage:
    python -m app.ingestion.cli --seasons 2016 2025
    python -m app.ingestion.cli --upcoming
    python -m app.ingestion.cli --seasons 2016 2025 --upcoming
    python -m app.ingestion.cli --team-stats 2016 2025
    python -m app.ingestion.cli --validate
"""

import argparse
import sys

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ingestion.fixtures import ingest_fixtures
from app.ingestion.team_stats import ingest_team_stats
from app.providers.afl.afltables import AFLTablesStatsProvider
from app.providers.afl.squiggle import SquiggleFixtureProvider
from app.validation.report import format_report
from app.validation.run import run_validation


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


def backfill_team_stats(
    db: Session, provider: AFLTablesStatsProvider, start_year: int, end_year: int
) -> tuple[list[int], list[str]]:
    """Ingests advanced team statistics for each season in [start_year,
    end_year] — one AFL Tables request per season (see
    app/providers/afl/afltables.py). Returns (failed_years, all_unmatched)."""
    failed_years: list[int] = []
    all_unmatched: list[str] = []
    for year in range(start_year, end_year + 1):
        print(f"Fetching AFL {year} team stats from AFL Tables...")
        try:
            rows = provider.get_season_team_stats("AFL", year)
        except Exception as exc:  # noqa: BLE001 — log and move to the next season
            print(f"  {year}: FAILED ({exc})")
            failed_years.append(year)
            continue
        result = ingest_team_stats(db, rows, season_year=year)
        print(
            f"  {year}: {result.rows_seen} rows seen | "
            f"created={result.stats_created} updated={result.stats_updated} "
            f"unchanged={result.stats_unchanged} unmatched={len(result.unmatched)}"
        )
        if result.unmatched:
            all_unmatched.extend(f"{year}: {msg}" for msg in result.unmatched)
    return failed_years, all_unmatched


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
        "--team-stats",
        nargs=2,
        type=int,
        metavar=("START_YEAR", "END_YEAR"),
        help="Backfill advanced team statistics from AFL Tables, inclusive, e.g. --team-stats 2016 2025. "
        "Requires fixture data for those seasons to already be ingested (--seasons first).",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.5,
        help="Seconds to wait between Squiggle requests (default 0.5 — be a good citizen of a free hobby API)",
    )
    parser.add_argument(
        "--team-stats-request-delay",
        type=float,
        default=1.0,
        help="Seconds to wait between AFL Tables requests (default 1.0 — one request per season)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run data-validation checks against the current database and print a PASS/WARNING/FAIL report. "
        "Combinable with --seasons/--upcoming to validate right after ingesting.",
    )
    args = parser.parse_args(argv)

    if not args.seasons and not args.upcoming and not args.team_stats and not args.validate:
        parser.print_help()
        return 1

    if args.seasons and args.seasons[0] > args.seasons[1]:
        parser.error("START_YEAR must be <= END_YEAR")
    if args.team_stats and args.team_stats[0] > args.team_stats[1]:
        parser.error("START_YEAR must be <= END_YEAR")

    db = SessionLocal()
    failed_years: list[int] = []
    failed_stat_years: list[int] = []
    unmatched_stats: list[str] = []
    report_has_failures = False
    try:
        if args.seasons or args.upcoming:
            provider = SquiggleFixtureProvider(request_delay_seconds=args.request_delay)
            if args.seasons:
                failed_years = backfill_seasons(db, provider, args.seasons[0], args.seasons[1])
            if args.upcoming:
                ingest_upcoming(db, provider)
        if args.team_stats:
            stats_provider = AFLTablesStatsProvider(request_delay_seconds=args.team_stats_request_delay)
            failed_stat_years, unmatched_stats = backfill_team_stats(
                db, stats_provider, args.team_stats[0], args.team_stats[1]
            )
            if unmatched_stats:
                print(f"\n{len(unmatched_stats)} team-stat rows could not be matched to a match:")
                for msg in unmatched_stats[:20]:
                    print(f"  {msg}")
                if len(unmatched_stats) > 20:
                    print(f"  ... and {len(unmatched_stats) - 20} more")
        if args.validate:
            report = run_validation(db)
            print()
            print(format_report(report))
            report_has_failures = report.has_failures
    finally:
        db.close()

    if failed_years:
        print(f"\nSeasons that failed and can be retried: {failed_years}")
        print(f"  e.g. python -m app.ingestion.cli --seasons {min(failed_years)} {max(failed_years)}")
        return 1

    if failed_stat_years:
        print(f"\nTeam-stats seasons that failed and can be retried: {failed_stat_years}")
        print(f"  e.g. python -m app.ingestion.cli --team-stats {min(failed_stat_years)} {max(failed_stat_years)}")
        return 1

    if report_has_failures:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
