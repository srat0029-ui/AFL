"""NBA data CLI — schedule, results and box scores from the stats provider
into the NBA tables, plus a validation report over what is stored.

  sync-teams        the 30 franchises (run once, before anything else)
  sync-schedule     games listed on each date in a range: past results and
                    future fixtures alike
  sync-box-scores   player game logs for FINAL games in a date range not yet
                    successfully fetched
  backfill          sync-teams, then sync-schedule and sync-box-scores across
                    a span of seasons
  validate          data-quality report over everything stored
  run-live-cycle    one pass of the live evidence cycle: schedule changes,
                    injury/availability feed, team rosters and depth charts,
                    lineups for games near tip-off, box scores for games
                    that have finished (see app/nba/live_cycle.py)

Usage:
  python -m app.nba.cli sync-teams
  python -m app.nba.cli sync-schedule --from 2026-10-20 --to 2026-10-31
  python -m app.nba.cli sync-box-scores --from 2026-01-15 --to 2026-01-17
  python -m app.nba.cli backfill --from-season 2015 --to-season 2026
  python -m app.nba.cli validate
  python -m app.nba.cli run-live-cycle

Every command is idempotent, and `backfill` is resumable: if it is
interrupted, run the same command again and it continues from where it
stopped (see app/nba/ingestion.py). Nothing here writes to any database
other than the one DATABASE_URL points at.

A season is named by the calendar year it STARTS in (2025 = 2025-26). The
two pandemic seasons did not follow the normal calendar and have their own
date ranges below; season membership of each game is always taken from the
provider, never inferred from the date.

One request is made per date and one per game, with a pause between
requests (see app/providers/nba/espn.py). A season is roughly 1,600
requests. No odds are fetched and no API quota is spent.
"""

import argparse
import sys
from datetime import date, datetime, timezone

from app.database import SessionLocal
from app.logging_config import configure_logging
from app.nba.ingestion import (
    BoxScoreIngestionReport,
    ScheduleSyncReport,
    sync_box_scores,
    sync_schedule,
    sync_teams,
)
from app.models.nba import COMPETITIVE_SEASON_TYPES, NbaSeasonType
from app.providers.nba.espn import DEFAULT_REQUEST_INTERVAL_SECONDS, PROVIDER_NAME, EspnNbaProvider

# (first date, last date) when a season did not run late September -> June.
_IRREGULAR_SEASON_DATES: dict[int, tuple[date, date]] = {
    2019: (date(2019, 9, 20), date(2020, 10, 12)),  # suspended in March 2020, finished in the Orlando bubble in October
    2020: (date(2020, 12, 1), date(2021, 7, 21)),  # started in December, Finals ended in July
}


def season_date_range(season_start_year: int) -> tuple[date, date]:
    """The span of calendar dates to scan for one season, preseason through
    the Finals."""
    if season_start_year in _IRREGULAR_SEASON_DATES:
        return _IRREGULAR_SEASON_DATES[season_start_year]
    return date(season_start_year, 9, 20), date(season_start_year + 1, 6, 30)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _print_schedule(report: ScheduleSyncReport) -> None:
    g = report.games
    print(f"dates requested: {report.dates_requested}  skipped (already settled): {report.dates_skipped_settled}  failed: {len(report.dates_failed)}")
    print(f"games seen: {g.seen}  created: {g.created}  updated: {g.updated}  unchanged: {g.unchanged}  skipped (unknown team): {len(g.skipped_unknown_team)}")
    for line in report.dates_failed[:10]:
        print(f"  FAILED {line}")


def _print_box_scores(report: BoxScoreIngestionReport) -> None:
    print(
        f"games considered: {report.games_seen}  ingested: {report.games_ingested} (of which partial: {report.games_ingested_partial})  "
        f"failed: {len(report.games_failed)}"
    )
    print(
        f"not final per box score: {len(report.games_not_final)}  final with no player rows: {len(report.games_without_lines)}  "
        f"rejected (points do not add up): {len(report.games_rejected)}"
    )
    for line in report.games_rejected[:10]:
        print(f"  REJECTED {line}")
    print(
        f"players created: {report.players_created}  renamed: {report.players_renamed}  "
        f"logs created: {report.logs_created}  updated: {report.logs_updated}  unchanged: {report.logs_unchanged}"
    )
    print(f"lines skipped - unknown team: {report.lines_skipped_unknown_team}  duplicate: {report.lines_skipped_duplicate}  malformed: {report.lines_malformed}")
    for line in report.games_failed[:10]:
        print(f"  FAILED {line}")


def _schedule_progress(day: date, report: ScheduleSyncReport) -> None:
    if report.dates_requested % 50 == 0:
        print(f"[{_stamp()}] schedule: {report.dates_requested} dates requested, at {day}, {report.games.created} games created", flush=True)


def _box_progress(game, report: BoxScoreIngestionReport) -> None:
    if report.games_seen % 100 == 0:
        print(
            f"[{_stamp()}] box scores: {report.games_seen} games, at {game.game_date}, {report.logs_created} logs created, "
            f"{len(report.games_rejected)} rejected, {len(report.games_failed)} failed",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.nba.cli", description="NBA data ingestion and validation")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync-teams")
    for name in ("sync-schedule", "sync-box-scores"):
        p = sub.add_parser(name)
        p.add_argument("--from", dest="start", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
        p.add_argument("--to", dest="end", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
        p.add_argument("--refresh", action="store_true", help="re-fetch what has already been fetched")
        if name == "sync-box-scores":
            p.add_argument("--retry-empty", action="store_true", help="also retry games whose box score was empty or rejected")
            p.add_argument("--include-preseason", action="store_true")
            p.add_argument("--limit", type=int, default=None, help="fetch at most this many games")
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--from-season", type=int, required=True, help="first season's starting year, e.g. 2015 for 2015-16")
    backfill.add_argument("--to-season", type=int, required=True, help="last season's starting year")
    for p in (backfill, *[sub.choices[n] for n in ("sync-schedule", "sync-box-scores")]):
        p.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL_SECONDS, help="seconds to pause between provider requests")
    live = sub.add_parser("run-live-cycle")
    live.add_argument("--force", action="store_true", help="poll everything now, ignoring the minimum intervals")
    validate = sub.add_parser("validate")
    validate.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    configure_logging("WARNING")
    db = SessionLocal()
    try:
        if args.command == "validate":
            from app.nba.validation import format_report, validate_nba_data

            report = validate_nba_data(db)
            print(report.to_json() if args.json else format_report(report))
            return 0

        if args.command == "run-live-cycle":
            from app.config import get_settings
            from app.nba.live_cycle import STEP_FAILED, run_live_cycle
            from app.providers.nba.espn_evidence import EspnNbaEvidenceProvider

            interval = get_settings().nba_request_interval_seconds
            run = run_live_cycle(
                db, EspnNbaProvider(request_interval_seconds=interval), EspnNbaEvidenceProvider(request_interval_seconds=interval),
                source=PROVIDER_NAME, force=args.force,
            )
            print(f"NBA live cycle run {run.id}: {run.status}")
            for step in run.steps:
                print(f"  {step['step']:<14} {step['status']:<8} {step['seconds']:>6}s  {step['detail']}")
            return 1 if any(step["status"] == STEP_FAILED for step in run.steps) else 0

        provider = EspnNbaProvider(request_interval_seconds=getattr(args, "request_interval", DEFAULT_REQUEST_INTERVAL_SECONDS))
        if args.command == "sync-teams":
            teams = sync_teams(db, provider)
            print(f"teams seen: {teams.seen}  created: {teams.created}  updated: {teams.updated}  renamed: {teams.renamed}")
            return 0

        if args.command == "backfill":
            if args.to_season < args.from_season:
                parser.error("--to-season must not be before --from-season")
            teams = sync_teams(db, provider)
            print(f"[{_stamp()}] teams seen: {teams.seen}  created: {teams.created}", flush=True)
            start, end = season_date_range(args.from_season)[0], season_date_range(args.to_season)[1]
            box_end = min(end, date.today())
        else:
            start, end = args.start, args.end
            box_end = end
        if end < start:
            parser.error("--to must not be before --from")

        failed = False
        if args.command in ("sync-schedule", "backfill"):
            schedule = sync_schedule(db, provider, start, end, source=PROVIDER_NAME, refresh=getattr(args, "refresh", False), on_progress=_schedule_progress)
            _print_schedule(schedule)
            failed = failed or bool(schedule.dates_failed)
        if args.command in ("sync-box-scores", "backfill"):
            season_types = COMPETITIVE_SEASON_TYPES
            if getattr(args, "include_preseason", False):
                season_types = (*season_types, NbaSeasonType.PRESEASON.value)
            boxes = sync_box_scores(
                db, provider, start, box_end, source=PROVIDER_NAME, refresh=getattr(args, "refresh", False),
                retry_empty=getattr(args, "retry_empty", False), season_types=season_types, limit=getattr(args, "limit", None), on_progress=_box_progress,
            )
            _print_box_scores(boxes)
            failed = failed or bool(boxes.games_failed)
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
