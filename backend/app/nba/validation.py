"""Data-quality report over the stored NBA history. Read-only.

The point of this module is to make "is this data safe to model on?" a
question with a checked answer rather than an assumption. It reports what
is there season by season and runs integrity checks that do not depend on
trusting the ingestion code — most usefully, that the points in a team's
player rows add up to that team's score in the game, which fails loudly if
a box score is partial, attached to the wrong game, or mis-parsed.

Expected season structure is stated explicitly, including the two seasons
that did NOT have the normal 1,230-game regular season. A season that
differs from its expected count is flagged for review; it is never assumed
to be wrong, and never silently assumed to be right.

Scope: "competitive" games are regular season, play-in and playoffs (see
COMPETITIVE_SEASON_TYPES). Preseason games are counted and reported but are
not part of the modelling dataset; All-Star and other exhibition events are
never stored, which the report confirms.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.nba import (
    COMPETITIVE_SEASON_TYPES,
    NbaBoxScoreState,
    NbaGame,
    NbaGameStatus,
    NbaPlayer,
    NbaPlayerGameLog,
    NbaScheduleSyncDate,
    NbaSeasonType,
    NbaTeam,
)

STANDARD_REGULAR_SEASON_GAMES = 1230  # 30 teams x 82 games / 2

# Seasons known NOT to have the standard structure, with the reason. Keyed by
# the year the season started.
ABNORMAL_SEASONS: dict[int, tuple[int, str]] = {
    2019: (1059, "2019-20 was suspended on 11 March 2020 and resumed with 22 teams in the Orlando bubble; teams played 63 to 75 games, not 82."),
    2020: (1080, "2020-21 was shortened to 72 games per team and ran December to July."),
}

# From 2023-24 the in-season tournament (NBA Cup) final is an 83rd game for
# its two finalists. The source lists it as a regular-season game, so the
# stored count is one more than the standings' 1,230. Verified in the data:
# the extra game is LAL-IND 2023-12-09, OKC-MIL 2024-12-17, NY-SA 2025-12-16.
FIRST_SEASON_WITH_CUP_FINAL = 2023
CUP_FINAL_NOTE = "includes the NBA Cup final, an 83rd game for the two finalists that the source lists as regular season"


def expected_regular_season_games(season_start_year: int) -> int:
    if season_start_year in ABNORMAL_SEASONS:
        return ABNORMAL_SEASONS[season_start_year][0]
    return STANDARD_REGULAR_SEASON_GAMES + (1 if season_start_year >= FIRST_SEASON_WITH_CUP_FINAL else 0)

# A team whose box score has fewer player rows than this is not a credible
# full box score (a team dresses at least eight).
MIN_CREDIBLE_ROWS_PER_TEAM = 8
SAMPLE_SIZE = 5


def season_label(season_start_year: int) -> str:
    return f"{season_start_year}-{str(season_start_year + 1)[-2:]}"


@dataclass
class SeasonValidation:
    season_start_year: int
    label: str
    games_total: int = 0
    games_by_type: dict[str, int] = field(default_factory=dict)
    games_by_status: dict[str, int] = field(default_factory=dict)
    regular_season_final: int = 0
    regular_season_expected: int = STANDARD_REGULAR_SEASON_GAMES
    postseason_final: int = 0
    player_game_logs: int = 0
    player_game_logs_played: int = 0
    unique_players: int = 0
    unique_teams: int = 0
    first_game_date: date | None = None
    last_game_date: date | None = None
    competitive_final_missing_box_score: int = 0
    games_with_suspect_logs: int = 0
    abnormal_note: str | None = None


@dataclass
class ValidationReport:
    generated_at: datetime
    games_total: int = 0
    player_game_logs_total: int = 0
    unique_players: int = 0
    unique_teams: int = 0
    first_game_date: date | None = None
    last_game_date: date | None = None
    seasons: list[SeasonValidation] = field(default_factory=list)
    box_score_states: dict[str, int] = field(default_factory=dict)
    # Integrity checks: a count, plus a few example ids to look at.
    duplicate_games: int = 0
    duplicate_player_game_logs: int = 0
    duplicate_players: int = 0
    same_matchup_same_date: list[str] = field(default_factory=list)
    logs_with_team_not_in_game: int = 0
    logs_without_player_or_game: int = 0
    players_without_logs: int = 0
    games_ingested_partial: int = 0
    games_points_mismatch: list[str] = field(default_factory=list)
    games_points_mismatch_count: int = 0
    games_thin_box_score: list[str] = field(default_factory=list)
    games_thin_box_score_count: int = 0
    games_wrong_starter_count: list[str] = field(default_factory=list)
    games_wrong_starter_count_count: int = 0
    played_logs_missing_minutes: int = 0
    played_logs_zero_minutes: int = 0
    dnp_logs: int = 0
    players_sharing_a_name: list[str] = field(default_factory=list)
    players_multiple_teams_in_a_season: int = 0
    schedule_dates_synced: int = 0
    schedule_dates_unsettled_in_past: int = 0
    flags: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=lambda v: v.isoformat())


_COMPETITIVE = NbaGame.season_type.in_(COMPETITIVE_SEASON_TYPES)
_FINAL = NbaGame.status == NbaGameStatus.FINAL.value


def _game_ref(source_game_id: str, game_date: date) -> str:
    return f"{source_game_id} ({game_date.isoformat()})"


def _per_team_box_rows(db: Session):
    """One row per (final competitive game, team): how the stored player rows
    for that team compare with the game's own score."""
    team_score = case((NbaPlayerGameLog.team_id == NbaGame.home_team_id, NbaGame.home_score), else_=NbaGame.away_score)
    return db.execute(
        select(
            NbaGame.id,
            NbaGame.source_game_id,
            NbaGame.game_date,
            NbaGame.season_start_year,
            NbaPlayerGameLog.team_id,
            func.count(NbaPlayerGameLog.id),
            func.coalesce(func.sum(NbaPlayerGameLog.points), 0),
            func.max(team_score),
            func.sum(case((NbaPlayerGameLog.started.is_(True), 1), else_=0)),
        )
        .join(NbaGame, NbaGame.id == NbaPlayerGameLog.game_id)
        .where(_FINAL, _COMPETITIVE)
        .group_by(NbaGame.id, NbaGame.source_game_id, NbaGame.game_date, NbaGame.season_start_year, NbaPlayerGameLog.team_id)
    ).all()


def validate_nba_data(db: Session, *, today: date | None = None) -> ValidationReport:
    today = today or date.today()
    report = ValidationReport(generated_at=datetime.now())
    seasons: dict[int, SeasonValidation] = {}

    def season(year: int) -> SeasonValidation:
        if year not in seasons:
            note = ABNORMAL_SEASONS[year][1] if year in ABNORMAL_SEASONS else None
            seasons[year] = SeasonValidation(season_start_year=year, label=season_label(year), regular_season_expected=expected_regular_season_games(year), abnormal_note=note)
        return seasons[year]

    # --- games ------------------------------------------------------------
    for year, season_type, status, n, first, last in db.execute(
        select(NbaGame.season_start_year, NbaGame.season_type, NbaGame.status, func.count(), func.min(NbaGame.game_date), func.max(NbaGame.game_date)).group_by(
            NbaGame.season_start_year, NbaGame.season_type, NbaGame.status
        )
    ).all():
        s = season(year)
        s.games_total += n
        s.games_by_type[season_type] = s.games_by_type.get(season_type, 0) + n
        s.games_by_status[status] = s.games_by_status.get(status, 0) + n
        s.first_game_date = min(filter(None, [s.first_game_date, first]))
        s.last_game_date = max(filter(None, [s.last_game_date, last]))
        if status == NbaGameStatus.FINAL.value:
            if season_type == NbaSeasonType.REGULAR.value:
                s.regular_season_final += n
            elif season_type in (NbaSeasonType.PLAY_IN.value, NbaSeasonType.PLAYOFFS.value):
                s.postseason_final += n

    # --- player game logs -------------------------------------------------
    for year, logs, played, players, teams in db.execute(
        select(
            NbaGame.season_start_year,
            func.count(NbaPlayerGameLog.id),
            func.sum(case((NbaPlayerGameLog.did_not_play.is_(False), 1), else_=0)),
            func.count(func.distinct(NbaPlayerGameLog.player_id)),
            func.count(func.distinct(NbaPlayerGameLog.team_id)),
        )
        .join(NbaGame, NbaGame.id == NbaPlayerGameLog.game_id)
        .group_by(NbaGame.season_start_year)
    ).all():
        s = season(year)
        s.player_game_logs, s.player_game_logs_played, s.unique_players, s.unique_teams = logs, int(played or 0), players, teams

    has_logs = select(NbaPlayerGameLog.id).where(NbaPlayerGameLog.game_id == NbaGame.id).exists()
    for year, n in db.execute(select(NbaGame.season_start_year, func.count()).where(_FINAL, _COMPETITIVE, ~has_logs).group_by(NbaGame.season_start_year)).all():
        season(year).competitive_final_missing_box_score = n

    # --- box-score integrity, per team per game ---------------------------
    mismatch: dict[int, tuple[str, date, int]] = {}
    thin: dict[int, tuple[str, date, int]] = {}
    teams_per_game: dict[int, int] = {}
    starters_per_game: dict[int, int] = {}
    game_refs: dict[int, tuple[str, date, int]] = {}
    partial_ids = set(db.scalars(select(NbaGame.id).where(NbaGame.box_score_state == NbaBoxScoreState.INGESTED_PARTIAL.value)).all())
    report.games_ingested_partial = len(partial_ids)
    for game_id, source_game_id, game_date, year, _team_id, rows, points, score, starters in _per_team_box_rows(db):
        ref = (source_game_id, game_date, year)
        game_refs[game_id] = ref
        teams_per_game[game_id] = teams_per_game.get(game_id, 0) + 1
        starters_per_game[game_id] = starters_per_game.get(game_id, 0) + int(starters or 0)
        # A partial game is short by exactly its unidentifiable player's
        # points, by construction; it is reported separately, not as a mismatch.
        if score is not None and int(points) != int(score) and game_id not in partial_ids:
            mismatch[game_id] = ref
        if rows < MIN_CREDIBLE_ROWS_PER_TEAM:
            thin[game_id] = ref
    for game_id, n_teams in teams_per_game.items():
        if n_teams != 2:
            thin[game_id] = game_refs[game_id]
    wrong_starters = {gid: game_refs[gid] for gid, n in starters_per_game.items() if n != 10}

    def _samples(found: dict[int, tuple[str, date, int]]) -> list[str]:
        return [_game_ref(sid, d) for sid, d, _ in sorted(found.values(), key=lambda r: r[1])[:SAMPLE_SIZE]]

    report.games_points_mismatch_count, report.games_points_mismatch = len(mismatch), _samples(mismatch)
    report.games_thin_box_score_count, report.games_thin_box_score = len(thin), _samples(thin)
    report.games_wrong_starter_count_count, report.games_wrong_starter_count = len(wrong_starters), _samples(wrong_starters)
    for game_id in set(mismatch) | set(thin):
        season(game_refs[game_id][2]).games_with_suspect_logs += 1

    # --- duplicates and broken relationships ------------------------------
    report.duplicate_games = len(db.execute(select(NbaGame.source, NbaGame.source_game_id).group_by(NbaGame.source, NbaGame.source_game_id).having(func.count() > 1)).all())
    report.duplicate_player_game_logs = len(
        db.execute(select(NbaPlayerGameLog.player_id, NbaPlayerGameLog.game_id).group_by(NbaPlayerGameLog.player_id, NbaPlayerGameLog.game_id).having(func.count() > 1)).all()
    )
    report.duplicate_players = len(db.execute(select(NbaPlayer.source, NbaPlayer.source_player_id).group_by(NbaPlayer.source, NbaPlayer.source_player_id).having(func.count() > 1)).all())
    report.same_matchup_same_date = [
        f"{d.isoformat()}: teams {home}/{away} x{n}"
        for d, home, away, n in db.execute(
            select(NbaGame.game_date, NbaGame.home_team_id, NbaGame.away_team_id, func.count())
            .where(NbaGame.status != NbaGameStatus.POSTPONED.value)
            .group_by(NbaGame.game_date, NbaGame.home_team_id, NbaGame.away_team_id)
            .having(func.count() > 1)
        ).all()
    ]
    report.logs_with_team_not_in_game = db.scalar(
        select(func.count())
        .select_from(NbaPlayerGameLog)
        .join(NbaGame, NbaGame.id == NbaPlayerGameLog.game_id)
        .where(and_(NbaPlayerGameLog.team_id != NbaGame.home_team_id, NbaPlayerGameLog.team_id != NbaGame.away_team_id))
    )
    report.logs_without_player_or_game = db.scalar(
        select(func.count())
        .select_from(NbaPlayerGameLog)
        .outerjoin(NbaPlayer, NbaPlayer.id == NbaPlayerGameLog.player_id)
        .outerjoin(NbaGame, NbaGame.id == NbaPlayerGameLog.game_id)
        .where(or_(NbaPlayer.id.is_(None), NbaGame.id.is_(None)))
    )
    report.players_without_logs = db.scalar(
        select(func.count()).select_from(NbaPlayer).where(~select(NbaPlayerGameLog.id).where(NbaPlayerGameLog.player_id == NbaPlayer.id).exists())
    )

    # --- minutes and participation ----------------------------------------
    played = NbaPlayerGameLog.did_not_play.is_(False)
    report.played_logs_missing_minutes = db.scalar(select(func.count()).select_from(NbaPlayerGameLog).where(played, NbaPlayerGameLog.minutes.is_(None)))
    report.played_logs_zero_minutes = db.scalar(select(func.count()).select_from(NbaPlayerGameLog).where(played, NbaPlayerGameLog.minutes == 0))
    report.dnp_logs = db.scalar(select(func.count()).select_from(NbaPlayerGameLog).where(NbaPlayerGameLog.did_not_play.is_(True)))

    # --- identity ---------------------------------------------------------
    report.players_sharing_a_name = [
        f"{name} x{n}" for name, n in db.execute(select(NbaPlayer.display_name, func.count()).group_by(NbaPlayer.display_name).having(func.count() > 1).order_by(NbaPlayer.display_name)).all()
    ]
    per_player_season = (
        select(NbaPlayerGameLog.player_id, NbaGame.season_start_year)
        .join(NbaGame, NbaGame.id == NbaPlayerGameLog.game_id)
        .group_by(NbaPlayerGameLog.player_id, NbaGame.season_start_year)
        .having(func.count(func.distinct(NbaPlayerGameLog.team_id)) > 1)
        .subquery()
    )
    report.players_multiple_teams_in_a_season = db.scalar(select(func.count()).select_from(per_player_season))

    # --- totals and bookkeeping -------------------------------------------
    report.games_total = db.scalar(select(func.count()).select_from(NbaGame))
    report.player_game_logs_total = db.scalar(select(func.count()).select_from(NbaPlayerGameLog))
    report.unique_players = db.scalar(select(func.count()).select_from(NbaPlayer))
    report.unique_teams = db.scalar(select(func.count()).select_from(NbaTeam))
    report.first_game_date, report.last_game_date = db.execute(select(func.min(NbaGame.game_date), func.max(NbaGame.game_date))).one()
    report.box_score_states = {
        (state or "not_attempted"): n for state, n in db.execute(select(NbaGame.box_score_state, func.count()).where(_FINAL, _COMPETITIVE).group_by(NbaGame.box_score_state)).all()
    }
    report.schedule_dates_synced = db.scalar(select(func.count()).select_from(NbaScheduleSyncDate))
    report.schedule_dates_unsettled_in_past = db.scalar(
        select(func.count()).select_from(NbaScheduleSyncDate).where(NbaScheduleSyncDate.is_settled.is_(False), NbaScheduleSyncDate.game_date < today)
    )

    report.seasons = [seasons[year] for year in sorted(seasons)]
    report.flags = _flags(report, today)
    return report


def _flags(report: ValidationReport, today: date) -> list[str]:
    flags: list[str] = []
    for s in report.seasons:
        if s.abnormal_note:
            flags.append(f"{s.label}: KNOWN ABNORMAL SEASON - {s.abnormal_note}")
        elif s.season_start_year >= FIRST_SEASON_WITH_CUP_FINAL:
            flags.append(f"{s.label}: expected count of {s.regular_season_expected} {CUP_FINAL_NOTE}.")
        in_progress_or_future = s.games_by_status.get(NbaGameStatus.SCHEDULED.value, 0) > 0 or (s.last_game_date is not None and s.last_game_date >= today)
        if in_progress_or_future:
            flags.append(f"{s.label}: season not complete ({s.games_by_status.get(NbaGameStatus.SCHEDULED.value, 0)} games still scheduled); counts are partial by design.")
        elif s.regular_season_final != s.regular_season_expected:
            flags.append(f"{s.label}: {s.regular_season_final} final regular-season games stored, {s.regular_season_expected} expected - review.")
        if s.competitive_final_missing_box_score:
            flags.append(f"{s.label}: {s.competitive_final_missing_box_score} final competitive game(s) have no player logs.")
        if s.games_with_suspect_logs:
            flags.append(f"{s.label}: {s.games_with_suspect_logs} game(s) with a suspect box score (points do not add up to the score, or too few player rows).")
        unsettled = {k: v for k, v in s.games_by_status.items() if k in (NbaGameStatus.POSTPONED.value, NbaGameStatus.IN_PROGRESS.value, NbaGameStatus.CANCELLED.value)}
        if unsettled:
            flags.append(f"{s.label}: games not final or scheduled: {unsettled}.")
        if s.unique_teams not in (0, 30):
            flags.append(f"{s.label}: {s.unique_teams} teams have player logs, expected 30.")
    for label, count in [
        ("duplicate games (same provider id)", report.duplicate_games),
        ("duplicate player game logs", report.duplicate_player_game_logs),
        ("duplicate players (same provider id)", report.duplicate_players),
        ("player logs whose team did not play in that game", report.logs_with_team_not_in_game),
        ("player logs without a player or game", report.logs_without_player_or_game),
    ]:
        if count:
            flags.append(f"INTEGRITY: {count} {label}.")
    if report.played_logs_missing_minutes:
        flags.append(f"{report.played_logs_missing_minutes} player row(s) are not marked did-not-play but have no minutes value in the source; minutes is left empty, not guessed.")
    if report.same_matchup_same_date:
        flags.append(f"INTEGRITY: {len(report.same_matchup_same_date)} date(s) with the same matchup stored more than once.")
    if report.games_ingested_partial:
        flags.append(f"{report.games_ingested_partial} game(s) are stored with one player row left out because the source published it without a player id.")
    if report.games_wrong_starter_count_count:
        flags.append(f"{report.games_wrong_starter_count_count} game(s) do not have exactly five starters per team in the source.")
    if report.players_sharing_a_name:
        flags.append(f"{len(report.players_sharing_a_name)} name(s) are held by more than one player id - possibly one person under two source ids; not merged.")
    failed = report.box_score_states.get(NbaBoxScoreState.FAILED.value, 0) + report.box_score_states.get("not_attempted", 0)
    if failed:
        flags.append(f"{failed} final competitive game(s) have a box score that failed or was never attempted - re-run the backfill.")
    return flags


def format_report(report: ValidationReport) -> str:
    lines = [
        f"NBA data validation - generated {report.generated_at:%Y-%m-%d %H:%M}",
        f"games: {report.games_total:,}   player game logs: {report.player_game_logs_total:,}   players: {report.unique_players:,}   teams: {report.unique_teams}",
        f"game dates: {report.first_game_date} to {report.last_game_date}",
        "",
        f"{'season':<9}{'games':>7}{'pre':>6}{'reg final':>11}{'expected':>10}{'post final':>12}{'sched':>7}{'logs':>9}{'played':>9}{'players':>9}{'teams':>7}{'no box':>8}{'suspect':>9}  dates",
    ]
    for s in report.seasons:
        lines.append(
            f"{s.label:<9}{s.games_total:>7}{s.games_by_type.get('preseason', 0):>6}{s.regular_season_final:>11}{s.regular_season_expected:>10}{s.postseason_final:>12}"
            f"{s.games_by_status.get('scheduled', 0):>7}{s.player_game_logs:>9}{s.player_game_logs_played:>9}{s.unique_players:>9}{s.unique_teams:>7}"
            f"{s.competitive_final_missing_box_score:>8}{s.games_with_suspect_logs:>9}  {s.first_game_date} to {s.last_game_date}"
        )
    lines += [
        "",
        f"box score outcomes (final competitive games): {report.box_score_states}",
        f"duplicates - games: {report.duplicate_games}  player game logs: {report.duplicate_player_game_logs}  players: {report.duplicate_players}  same matchup/date: {len(report.same_matchup_same_date)}",
        f"relationships - logs with team not in game: {report.logs_with_team_not_in_game}  logs without player/game: {report.logs_without_player_or_game}  players with no logs: {report.players_without_logs}",
        f"box score integrity - points do not sum to score: {report.games_points_mismatch_count} {report.games_points_mismatch}",
        f"                      stored with an unidentifiable player row left out: {report.games_ingested_partial}",
        f"                      thin box score: {report.games_thin_box_score_count} {report.games_thin_box_score}",
        f"                      starters != 10: {report.games_wrong_starter_count_count} {report.games_wrong_starter_count}",
        f"participation - DNP rows: {report.dnp_logs:,}  played with no minutes value: {report.played_logs_missing_minutes}  played with 0 minutes: {report.played_logs_zero_minutes}",
        f"identity - names shared by more than one player: {len(report.players_sharing_a_name)} {report.players_sharing_a_name[:8]}",
        f"           player-seasons with more than one team: {report.players_multiple_teams_in_a_season}",
        f"schedule dates synced: {report.schedule_dates_synced}  past dates still unsettled: {report.schedule_dates_unsettled_in_past}",
        "",
        "FLAGS:" if report.flags else "FLAGS: none",
    ]
    lines += [f"  - {flag}" for flag in report.flags]
    return "\n".join(lines)
