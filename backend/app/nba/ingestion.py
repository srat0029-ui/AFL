"""Writes what an NBA stats provider reports into the NBA reference/result
tables: teams, games, players, player game logs. Provider-agnostic: it
consumes app/providers/nba/types.py records, never a source's raw response.

Idempotent and resumable
------------------------
Re-running any step against the same source data changes nothing, and a
backfill that is interrupted can simply be started again:

- Every write is an upsert on a provider id, so nothing is duplicated.
- Each game and each date is committed on its own, so an interruption loses
  at most the item in flight.
- The schedule sync records each date it has fetched (NbaScheduleSyncDate)
  and does not re-request a past date whose games were all settled.
- The box-score sync records each game's outcome (NbaGame.box_score_state)
  and by default only fetches final games not yet successfully handled.

Identity
--------
Nothing is matched on a display name alone.

- Team: by the provider's team id (held in `external_ids`). A name is only
  used once, to attach a provider id to a team row that has none. A
  provider renaming a team updates the name on the same row.
- Game: by (source, source_game_id).
- Player: by (source, source_player_id). Two players with the same name are
  two rows; one player whose published name changes stays one row, with the
  earlier spelling kept in `source_metadata["name_variants"]`.
- Game log: by (player, game, source). `team_id` is the team the player
  appeared for in THAT game, so a traded player's history stays correct.
  `NbaPlayer.current_team_id` follows the player's most recent game only.

Results, not partial or placeholder results
------------------------------------------
A box score is stored only if it passes two gates:

1. The box score ITSELF reports the game as final. A half-finished game's
   running totals would settle props wrongly and feed false history to a
   model.
2. For each team, the points in its player rows add up exactly to the
   team's score. The source publishes empty placeholder box scores for some
   older games (every player at "--" minutes and 0 points); stored as-is
   they would be indistinguishable from a real 0-point night. A box score
   that fails is rejected whole - one wrong player line cannot be told
   apart from another - and the game is recorded as having no usable box
   score rather than a wrong one.

One case is kept rather than rejected: a player row the source published
with no player id. It cannot be attached to anyone, so that player is
absent, but its points are counted in the check; if the totals then
reconcile, every other line is trustworthy and is stored, and the game is
marked `ingested_partial` so the gap is visible.

These rows are facts about the past (or the schedule), not beliefs, so the
prospective boundary is enforced where they are READ for prediction
(app/nba/asof.py), not here.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.prospective import ensure_utc
from app.models.nba import COMPETITIVE_SEASON_TYPES, NbaBoxScoreState, NbaGame, NbaGameStatus, NbaPlayer, NbaPlayerGameLog, NbaScheduleSyncDate, NbaTeam
from app.providers.nba.types import NbaBoxScore, NbaGameRecord, NbaPlayerBoxLine, NbaTeamRecord

logger = logging.getLogger(__name__)

# A past date is only treated as settled once it is this many days old, so a
# late-finishing or late-corrected game day is fetched at least once more.
SETTLED_AFTER_DAYS = 2


class NbaStatsProvider(Protocol):
    def get_teams(self) -> list[NbaTeamRecord]: ...

    def get_games(self, on: date, *, team_ids: set[str] | None = None) -> list[NbaGameRecord]: ...

    def get_box_score(self, source_game_id: str) -> NbaBoxScore: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _same(current, new) -> bool:
    # SQLite hands back naive datetimes for timezone-aware columns.
    if isinstance(current, datetime) and isinstance(new, datetime):
        return ensure_utc(current) == ensure_utc(new)
    return current == new


# --- Teams -----------------------------------------------------------------


@dataclass
class TeamIngestionReport:
    seen: int = 0
    created: int = 0
    updated: int = 0
    renamed: list[str] = field(default_factory=list)


def teams_by_source_id(db: Session, source: str) -> dict[str, NbaTeam]:
    return {team.external_ids[source]: team for team in db.scalars(select(NbaTeam)).all() if team.external_ids and source in team.external_ids}


def ingest_teams(db: Session, records: list[NbaTeamRecord]) -> TeamIngestionReport:
    report = TeamIngestionReport()
    all_teams = list(db.scalars(select(NbaTeam)).all())
    for record in records:
        report.seen += 1
        team = next((t for t in all_teams if (t.external_ids or {}).get(record.source) == record.source_team_id), None)
        if team is None:
            # No row carries this provider id yet. A row with the same name
            # and no id for this provider is the same franchise seen through
            # a different source; anything else is a new team.
            team = next((t for t in all_teams if t.name == record.name and record.source not in (t.external_ids or {})), None)
            if team is None:
                team = NbaTeam(name=record.name, abbreviation=record.abbreviation, external_ids={record.source: record.source_team_id})
                db.add(team)
                all_teams.append(team)
                report.created += 1
                continue
            team.external_ids = {**(team.external_ids or {}), record.source: record.source_team_id}
            report.updated += 1
        if team.name != record.name:
            report.renamed.append(f"{team.name} -> {record.name}")
            team.name = record.name
        if team.abbreviation != record.abbreviation:
            team.abbreviation = record.abbreviation
            report.updated += 1
    db.commit()
    return report


# --- Games -----------------------------------------------------------------


@dataclass
class GameIngestionReport:
    seen: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_unknown_team: list[str] = field(default_factory=list)


_GAME_FIELDS = ("season_start_year", "season_type", "game_date", "scheduled_start", "status", "home_score", "away_score", "source_status", "source_season_type")


def ingest_games(db: Session, records: list[NbaGameRecord], report: GameIngestionReport | None = None, *, now: datetime | None = None) -> GameIngestionReport:
    report = report or GameIngestionReport()
    if not records:
        return report
    now = now or _utcnow()
    source = records[0].source
    teams = teams_by_source_id(db, source)
    existing = {
        game.source_game_id: game
        for game in db.scalars(select(NbaGame).where(NbaGame.source == source, NbaGame.source_game_id.in_([r.source_game_id for r in records]))).all()
    }
    for record in records:
        report.seen += 1
        home, away = teams.get(record.home_source_team_id), teams.get(record.away_source_team_id)
        if home is None or away is None:
            report.skipped_unknown_team.append(f"{record.source_game_id}: {record.away_team_name} @ {record.home_team_name}")
            continue
        game = existing.get(record.source_game_id)
        if game is None:
            game = NbaGame(
                source=record.source, source_game_id=record.source_game_id, home_team_id=home.id, away_team_id=away.id, source_synced_at=now,
                **{name: getattr(record, name) for name in _GAME_FIELDS},
            )
            db.add(game)
            existing[record.source_game_id] = game  # the same game listed twice in one batch is one row
            report.created += 1
            continue
        changed = False
        for name in _GAME_FIELDS:
            if not _same(getattr(game, name), getattr(record, name)):
                setattr(game, name, getattr(record, name))
                changed = True
        if game.home_team_id != home.id or game.away_team_id != away.id:
            game.home_team_id, game.away_team_id = home.id, away.id
            changed = True
        game.source_synced_at = now
        if changed:
            report.updated += 1
        else:
            report.unchanged += 1
    db.commit()
    return report


# --- Box scores ------------------------------------------------------------


@dataclass
class BoxScoreIngestionReport:
    games_seen: int = 0
    games_ingested: int = 0
    games_ingested_partial: int = 0  # stored, but with a player row that had no usable id left out
    games_not_final: list[str] = field(default_factory=list)
    games_without_lines: list[str] = field(default_factory=list)
    games_rejected: list[str] = field(default_factory=list)
    games_failed: list[str] = field(default_factory=list)
    players_created: int = 0
    players_renamed: int = 0
    logs_created: int = 0
    logs_updated: int = 0
    logs_unchanged: int = 0
    lines_skipped_unknown_team: int = 0
    lines_skipped_duplicate: int = 0
    lines_malformed: int = 0


_STAT_FIELDS = tuple(f.name for f in fields(NbaPlayerBoxLine) if f.name not in ("source_player_id", "player_name", "position", "source_team_id"))


def _set_box_state(db: Session, game: NbaGame, state: NbaBoxScoreState, when: datetime) -> None:
    game.box_score_state = state.value
    game.box_score_synced_at = when
    db.commit()


def _follow_latest_game(player: NbaPlayer, line: NbaPlayerBoxLine, team: NbaTeam, game: NbaGame, report: BoxScoreIngestionReport) -> None:
    """Keep the player's display fields in step with their MOST RECENT game
    only. Re-ingesting an older game must not move a traded player back to
    a former team or restore an old spelling of their name."""
    tipoff = ensure_utc(game.scheduled_start)
    if player.last_game_at is not None and tipoff < ensure_utc(player.last_game_at):
        return
    player.last_game_at = tipoff
    player.current_team_id = team.id
    if line.position:
        player.position = line.position
    if line.player_name != player.display_name:
        variants = list((player.source_metadata or {}).get("name_variants", []))
        if player.display_name not in variants:
            variants.append(player.display_name)
        player.source_metadata = {**(player.source_metadata or {}), "name_variants": variants}
        player.display_name = line.player_name
        report.players_renamed += 1


def points_discrepancies(game: NbaGame, box: NbaBoxScore, source_team_id_by_team_id: dict[int, str]) -> list[str]:
    """Why this box score's player rows cannot be trusted, per team; empty
    when every team's player points add up to its score. The score checked
    against is the one stored on the game (from the schedule feed, fetched
    independently of the box score), falling back to the box score's own
    header."""
    problems: list[str] = []
    for team_id, stored_score in ((game.home_team_id, game.home_score), (game.away_team_id, game.away_score)):
        source_team_id = source_team_id_by_team_id.get(team_id)
        score = stored_score if stored_score is not None else (box.team_scores or {}).get(source_team_id)
        if score is None:
            problems.append(f"team {source_team_id}: no score to check against")
            continue
        counted: set[str] = set()
        total = (box.unattributed_points or {}).get(source_team_id, 0)
        for line in box.lines:
            # A player listed twice is counted once, as ingestion stores them once.
            if line.source_team_id != source_team_id or line.did_not_play or line.source_player_id in counted:
                continue
            counted.add(line.source_player_id)
            total += line.points or 0
        if total != score:
            problems.append(f"team {source_team_id}: player points {total} != score {score}")
    return problems


def ingest_box_score(db: Session, game: NbaGame, box: NbaBoxScore, report: BoxScoreIngestionReport | None = None) -> BoxScoreIngestionReport:
    """Write one game's player logs, and record the outcome on the game.
    Stores nothing unless the box score reports the game as final AND its
    player points add up to each team's score."""
    report = report or BoxScoreIngestionReport()
    report.games_seen += 1
    report.lines_malformed += box.malformed_rows
    if box.status != NbaGameStatus.FINAL.value:
        report.games_not_final.append(game.source_game_id)
        _set_box_state(db, game, NbaBoxScoreState.NOT_FINAL, box.fetched_at)
        return report
    if not box.lines:
        # Final, but the source published no player rows. Recorded so it is
        # visible, rather than counted as a successfully ingested game.
        report.games_without_lines.append(game.source_game_id)
        _set_box_state(db, game, NbaBoxScoreState.NO_PLAYER_ROWS, box.fetched_at)
        return report

    teams = teams_by_source_id(db, box.source)
    problems = points_discrepancies(game, box, {team.id: source_id for source_id, team in teams.items()})
    if problems:
        report.games_rejected.append(f"{game.source_game_id}: {'; '.join(problems)}")
        _set_box_state(db, game, NbaBoxScoreState.REJECTED, box.fetched_at)
        return report
    side_by_team_id = {game.home_team_id: (game.away_team_id, True), game.away_team_id: (game.home_team_id, False)}
    players = {
        p.source_player_id: p
        for p in db.scalars(select(NbaPlayer).where(NbaPlayer.source == box.source, NbaPlayer.source_player_id.in_([line.source_player_id for line in box.lines]))).all()
    }
    logs = {log.player_id: log for log in db.scalars(select(NbaPlayerGameLog).where(NbaPlayerGameLog.game_id == game.id, NbaPlayerGameLog.source == box.source)).all()}

    seen_in_this_box: set[str] = set()
    for line in box.lines:
        team = teams.get(line.source_team_id)
        if team is None or team.id not in side_by_team_id:
            report.lines_skipped_unknown_team += 1
            continue
        if line.source_player_id in seen_in_this_box:
            report.lines_skipped_duplicate += 1
            continue
        seen_in_this_box.add(line.source_player_id)
        opponent_team_id, is_home = side_by_team_id[team.id]

        player = players.get(line.source_player_id)
        if player is None:
            player = NbaPlayer(display_name=line.player_name, position=line.position, source=box.source, source_player_id=line.source_player_id)
            db.add(player)
            db.flush()
            players[line.source_player_id] = player
            report.players_created += 1
        _follow_latest_game(player, line, team, game, report)

        log = logs.get(player.id)
        if log is None:
            db.add(
                NbaPlayerGameLog(
                    player_id=player.id, game_id=game.id, team_id=team.id, opponent_team_id=opponent_team_id, is_home=is_home,
                    source=box.source, recorded_at=box.fetched_at, **{name: getattr(line, name) for name in _STAT_FIELDS},
                )
            )
            report.logs_created += 1
            continue
        changed = any(getattr(log, name) != getattr(line, name) for name in _STAT_FIELDS) or log.team_id != team.id
        if changed:
            for name in _STAT_FIELDS:
                setattr(log, name, getattr(line, name))
            log.team_id, log.opponent_team_id, log.is_home = team.id, opponent_team_id, is_home
            log.recorded_at = box.fetched_at
            report.logs_updated += 1
        else:
            report.logs_unchanged += 1

    if game.status != NbaGameStatus.FINAL.value:
        game.status = NbaGameStatus.FINAL.value
    partial = box.malformed_rows > 0
    _set_box_state(db, game, NbaBoxScoreState.INGESTED_PARTIAL if partial else NbaBoxScoreState.INGESTED, box.fetched_at)
    report.games_ingested += 1
    report.games_ingested_partial += int(partial)
    return report


# --- Orchestration ---------------------------------------------------------


def _dates(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


@dataclass
class ScheduleSyncReport:
    dates_requested: int = 0
    dates_skipped_settled: int = 0
    dates_failed: list[str] = field(default_factory=list)
    games: GameIngestionReport = field(default_factory=GameIngestionReport)


def sync_teams(db: Session, provider: NbaStatsProvider) -> TeamIngestionReport:
    return ingest_teams(db, provider.get_teams())


_SETTLED_STATUSES = (NbaGameStatus.FINAL.value, NbaGameStatus.CANCELLED.value)


def sync_schedule(
    db: Session,
    provider: NbaStatsProvider,
    start: date,
    end: date,
    *,
    source: str,
    refresh: bool = False,
    today: date | None = None,
    on_progress: Callable[[date, ScheduleSyncReport], None] | None = None,
) -> ScheduleSyncReport:
    """Fetch and upsert every game listed on each date in [start, end] —
    past results and future fixtures alike. Only games between two known
    NBA teams are kept, so teams must be synced first.

    A past date already fetched with nothing left unsettled is skipped
    unless `refresh` is set; that is what lets an interrupted backfill
    resume where it stopped."""
    report = ScheduleSyncReport()
    team_ids = set(teams_by_source_id(db, source))
    if not team_ids:
        raise ValueError(f"no NBA teams with a {source!r} id are stored - run the team sync first")
    today = today or _utcnow().date()
    checkpoints = {
        c.game_date: c
        for c in db.scalars(select(NbaScheduleSyncDate).where(NbaScheduleSyncDate.source == source, NbaScheduleSyncDate.game_date >= start, NbaScheduleSyncDate.game_date <= end)).all()
    }
    for day in _dates(start, end):
        checkpoint = checkpoints.get(day)
        if checkpoint is not None and checkpoint.is_settled and not refresh:
            report.dates_skipped_settled += 1
            continue
        report.dates_requested += 1
        try:
            records = provider.get_games(day, team_ids=team_ids)
        except Exception as exc:  # one bad date must not abort a multi-season backfill
            logger.warning("nba.sync_schedule.date_failed date=%s error=%s", day.isoformat(), str(exc)[:200])
            report.dates_failed.append(f"{day.isoformat()}: {str(exc)[:120]}")
            continue
        now = _utcnow()
        ingest_games(db, records, report.games, now=now)
        settled = day <= today - timedelta(days=SETTLED_AFTER_DAYS) and all(r.status in _SETTLED_STATUSES for r in records)
        if checkpoint is None:
            db.add(NbaScheduleSyncDate(source=source, game_date=day, synced_at=now, games_kept=len(records), is_settled=settled))
        else:
            checkpoint.synced_at, checkpoint.games_kept, checkpoint.is_settled = now, len(records), settled
        db.commit()
        if on_progress is not None:
            on_progress(day, report)
    return report


def sync_box_scores(
    db: Session,
    provider: NbaStatsProvider,
    start: date,
    end: date,
    *,
    source: str,
    refresh: bool = False,
    retry_empty: bool = False,
    season_types: tuple[str, ...] = COMPETITIVE_SEASON_TYPES,
    limit: int | None = None,
    on_progress: Callable[[NbaGame, BoxScoreIngestionReport], None] | None = None,
) -> BoxScoreIngestionReport:
    """Fetch box scores for FINAL games dated in [start, end].

    By default only games not yet successfully handled are fetched: never
    attempted, failed, or not final last time. A game the source published
    with no player rows is not retried unless `retry_empty`; `refresh`
    re-fetches everything (picking up stat corrections); a rejected box
    score counts as empty for this purpose. Preseason box
    scores are not fetched unless `season_types` asks for them."""
    report = BoxScoreIngestionReport()
    stmt = (
        select(NbaGame)
        .where(
            NbaGame.source == source, NbaGame.status == NbaGameStatus.FINAL.value, NbaGame.game_date >= start, NbaGame.game_date <= end,
            NbaGame.season_type.in_(season_types),
        )
        .order_by(NbaGame.scheduled_start, NbaGame.id)
    )
    if not refresh:
        pending = [NbaBoxScoreState.FAILED.value, NbaBoxScoreState.NOT_FINAL.value]
        if retry_empty:
            pending += [NbaBoxScoreState.NO_PLAYER_ROWS.value, NbaBoxScoreState.REJECTED.value]
        stmt = stmt.where(or_(NbaGame.box_score_state.is_(None), NbaGame.box_score_state.in_(pending)))
    games = db.scalars(stmt).all()
    for game in games[:limit] if limit is not None else games:
        try:
            box = provider.get_box_score(game.source_game_id)
        except Exception as exc:
            logger.warning("nba.sync_box_scores.game_failed game=%s error=%s", game.source_game_id, str(exc)[:200])
            report.games_seen += 1
            report.games_failed.append(f"{game.source_game_id}: {str(exc)[:120]}")
            db.rollback()
            _set_box_state(db, game, NbaBoxScoreState.FAILED, _utcnow())
        else:
            ingest_box_score(db, game, box, report)
        if on_progress is not None:
            on_progress(game, report)
    return report
