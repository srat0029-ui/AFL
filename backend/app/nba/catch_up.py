"""Bounded catch-up of missed CURRENT-season results: the schedule and box
scores for a short window of past dates that the live cycle can no longer
reach (its schedule and box-score steps only look back
`nba_schedule_lookback_days`). Written for the 2026-27 gap left by the
Layerbase outage (no collection 9-28 October 2026).

It is the existing idempotent ingestion, nothing new:

- `sync_schedule` for the window, so games the database still holds as
  "scheduled" become final with their scores. Every schedule observation is
  stamped with the time it is actually made (now), never with the game's
  date, so nothing implies the result was known earlier. No per-game
  schedule confirmations are recorded: those are the live cycle's evidence
  of a pregame re-listing, and a result fetched days later is not one.
- `sync_box_scores` for competitive FINAL games not yet successfully
  handled. Stored box scores are never re-fetched or rewritten (no
  `refresh`), and `recorded_at` is the actual fetch time. Players' display
  fields follow the most recent game, exactly as in the live cycle; an older
  game can never move a player back (see ingestion._follow_latest_game).

It never touches snapshots, outlooks, predictions, evidence polls, rosters,
the injury feed or any AFL table.

Window rules (refused otherwise, before any request is made):
- inside the current season's date range;
- ends before the live cycle's lookback window, so the two never write the
  same games;
- at most MAX_WINDOW_DAYS dates.

`window_coverage` is the read-only check, run before and after."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.nba import COMPETITIVE_SEASON_TYPES, NbaGame, NbaGameStatus, NbaPlayerGameLog, NbaScheduleSyncDate
from app.nba.ingestion import BoxScoreIngestionReport, NbaStatsProvider, ScheduleSyncReport, sync_box_scores, sync_schedule
from app.nba.validation import STORED_BOX_SCORE_STATES

MAX_WINDOW_DAYS = 31

_EXCLUDED_STATUSES = (NbaGameStatus.POSTPONED.value, NbaGameStatus.CANCELLED.value)


class CatchUpWindowError(ValueError):
    pass


def current_season_start_year(today: date) -> int:
    """Seasons start in the autumn: July onward belongs to the season that
    starts that calendar year."""
    return today.year if today.month >= 7 else today.year - 1


def validate_window(start: date, end: date, *, today: date, lookback_days: int) -> int:
    """The season the window belongs to, or CatchUpWindowError."""
    from app.nba.cli import season_date_range  # the CLI owns the season calendar

    if end < start:
        raise CatchUpWindowError("--to must not be before --from")
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise CatchUpWindowError(f"window is {(end - start).days + 1} dates; at most {MAX_WINDOW_DAYS} are allowed")
    season = current_season_start_year(today)
    first, last = season_date_range(season)
    if start < first or end > last:
        raise CatchUpWindowError(f"window must lie inside the current season ({first} to {last}); this is not a historical backfill")
    latest = today - timedelta(days=lookback_days + 1)
    if end > latest:
        raise CatchUpWindowError(
            f"--to must be on or before {latest}: the live cycle itself covers the last {lookback_days} days, and the two must never write the same games"
        )
    return season


@dataclass
class WindowCoverage:
    start: date
    end: date
    complete: bool = True
    competitive_games: int = 0
    stored: int = 0
    player_logs: int = 0
    excluded: list[str] = field(default_factory=list)  # postponed / cancelled: nothing to fetch
    problems: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def window_coverage(db: Session, start: date, end: date, *, source: str) -> WindowCoverage:
    """Read-only. The window is complete when every date has been fetched at
    least once and every competitive game dated in it is final with a stored
    box score (postponed and cancelled games excepted). Anything else is
    listed as a problem: a date never fetched, a game still held as
    scheduled / in progress, or a final game whose box score is missing,
    failed, empty or rejected."""
    cov = WindowCoverage(start=start, end=end)
    checkpoints = {
        c.game_date: c
        for c in db.scalars(select(NbaScheduleSyncDate).where(NbaScheduleSyncDate.source == source, NbaScheduleSyncDate.game_date >= start, NbaScheduleSyncDate.game_date <= end)).all()
    }
    games = db.scalars(
        select(NbaGame)
        .where(NbaGame.source == source, NbaGame.game_date >= start, NbaGame.game_date <= end, NbaGame.season_type.in_(COMPETITIVE_SEASON_TYPES))
        .order_by(NbaGame.game_date, NbaGame.scheduled_start, NbaGame.id)
    ).all()
    logs_by_game = dict(
        db.execute(
            select(NbaPlayerGameLog.game_id, func.count(NbaPlayerGameLog.id))
            .where(NbaPlayerGameLog.game_id.in_([g.id for g in games]), NbaPlayerGameLog.source == source)
            .group_by(NbaPlayerGameLog.game_id)
        ).all()
    ) if games else {}
    day = start
    while day <= end:
        checkpoint = checkpoints.get(day)
        todays = [g for g in games if g.game_date == day]
        stored = [g for g in todays if g.status == NbaGameStatus.FINAL.value and g.box_score_state in STORED_BOX_SCORE_STATES]
        logs = sum(logs_by_game.get(g.id, 0) for g in stored)
        if checkpoint is None:
            cov.problems.append(f"{day}: schedule never fetched")
        for g in todays:
            if g.status in _EXCLUDED_STATUSES:
                cov.excluded.append(f"{day} {g.source_game_id}: {g.status}")
            elif g.status != NbaGameStatus.FINAL.value:
                cov.problems.append(f"{day} {g.source_game_id}: status {g.status} (schedule not refreshed since the game)")
            elif g.box_score_state not in STORED_BOX_SCORE_STATES:
                cov.problems.append(f"{day} {g.source_game_id}: final, box score {g.box_score_state or 'never fetched'}")
        synced = f"last fetched {checkpoint.synced_at:%Y-%m-%d %H:%MZ}{', settled' if checkpoint.is_settled else ''}" if checkpoint else "never fetched"
        cov.lines.append(f"  {day}  competitive games {len(todays):>2}  with stored box score {len(stored):>2}  player logs {logs:>4}  ({synced})")
        cov.competitive_games += len(todays)
        cov.stored += len(stored)
        cov.player_logs += logs
        day += timedelta(days=1)
    cov.complete = not cov.problems
    cov.lines.insert(0, f"window {start} to {end}: {'COMPLETE' if cov.complete else 'INCOMPLETE'}")
    cov.lines.append(f"  total: competitive games {cov.competitive_games}  with stored box score {cov.stored}  player logs {cov.player_logs}")
    cov.lines += [f"  excluded {line}" for line in cov.excluded] + [f"  MISSING {line}" for line in cov.problems]
    return cov


@dataclass
class CatchUpResult:
    season: int
    schedule: ScheduleSyncReport
    box_scores: BoxScoreIngestionReport
    coverage: WindowCoverage


def run_catch_up(
    db: Session, provider: NbaStatsProvider, start: date, end: date, *, source: str, lookback_days: int, today: date | None = None, now: datetime | None = None
) -> CatchUpResult:
    """Validate the window, then fetch its schedule and missing box scores.
    Resumable: rerunning skips dates already settled and games already
    stored. `now` overrides the observation time (tests only)."""
    today = today or datetime.now(timezone.utc).date()
    season = validate_window(start, end, today=today, lookback_days=lookback_days)
    schedule = sync_schedule(db, provider, start, end, source=source, today=today, now=now, record_confirmations=False)
    boxes = sync_box_scores(db, provider, start, end, source=source, season_types=COMPETITIVE_SEASON_TYPES)
    return CatchUpResult(season=season, schedule=schedule, box_scores=boxes, coverage=window_coverage(db, start, end, source=source))
