"""Bounded current-season catch-up (app/nba/catch_up.py): the window rules,
that it fills only missing games, that every timestamp is the real
observation time, that it is idempotent, and that nothing outside the
schedule / box-score tables changes. Providers are in-memory fakes."""

import argparse
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.prospective import ensure_utc
from app.database import Base
from app.models.nba import NbaEvidencePoll, NbaGame, NbaGameScheduleObservation, NbaPlayer, NbaPlayerGameLog, NbaTeam
from app.nba.catch_up import MAX_WINDOW_DAYS, CatchUpWindowError, current_season_start_year, run_catch_up, validate_window, window_coverage
from app.nba.cli import _catch_up
from app.nba.ingestion import ingest_box_score, sync_schedule, sync_teams
from tests.test_nba_live_cycle import FakeStats, _record
from tests.test_nba_live_evidence import SRC

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "nba-current-season-catch-up.yml"
TODAY = date(2026, 10, 28)
BEFORE_OUTAGE = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)  # last schedule sync before collection stopped
NOW = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)
LOOKBACK = 3
START, END = date(2026, 10, 20), date(2026, 10, 24)


def _tip(day: int) -> datetime:
    return datetime(2026, 10, day, 23, 30, tzinfo=timezone.utc)


def _final(game_id: str, day: int, **kwargs):
    return _record(game_id, _tip(day), status="final", home_score=100, away_score=90, **kwargs)


def _outage_world(db):
    """Teams synced; g21 and g23 were listed as scheduled on 9 October (the
    last sync before the outage); g24 was never listed. The provider now
    reports all three final."""
    stats = FakeStats([_record("g21", _tip(21)), _record("g23", _tip(23))])
    sync_teams(db, stats)
    sync_schedule(db, stats, START, date(2026, 10, 23), source=SRC, today=date(2026, 10, 9), now=BEFORE_OUTAGE)
    stats.games = [_final("g21", 21), _final("g23", 23), _final("g24", 24)]
    stats.calls.clear()
    return stats


def _row_counts(db) -> dict[str, int]:
    return {t.name: db.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables}


# --- window rules ---------------------------------------------------------


def test_the_outage_window_is_accepted():
    assert validate_window(START, END, today=TODAY, lookback_days=LOOKBACK) == 2026
    assert current_season_start_year(date(2027, 3, 1)) == 2026 and current_season_start_year(date(2026, 7, 1)) == 2026


@pytest.mark.parametrize(
    ("start", "end", "why"),
    [
        (date(2026, 10, 24), date(2026, 10, 20), "before"),
        (date(2026, 10, 20), date(2026, 10, 25), "live cycle"),  # 25 Oct is inside the live cycle's lookback on 28 Oct
        (date(2026, 10, 20), date(2026, 10, 28), "live cycle"),
        (date(2026, 4, 1), date(2026, 4, 10), "current season"),  # 2025-26: a historical backfill, not a catch-up
        (date(2026, 9, 1), date(2026, 9, 21), "current season"),  # before the season's first date
        (date(2026, 9, 20), date(2026, 9, 20) + timedelta(days=MAX_WINDOW_DAYS), "at most"),
    ],
)
def test_windows_outside_the_rules_are_refused(start, end, why):
    with pytest.raises(CatchUpWindowError, match=why):
        validate_window(start, end, today=TODAY, lookback_days=LOOKBACK)


def test_a_refused_window_makes_no_request_and_writes_nothing(db_session):
    stats = _outage_world(db_session)
    before = _row_counts(db_session)
    with pytest.raises(CatchUpWindowError):
        run_catch_up(db_session, stats, START, date(2026, 10, 26), source=SRC, lookback_days=LOOKBACK, today=TODAY, now=NOW)
    assert stats.calls == [] and _row_counts(db_session) == before


# --- filling the gap ------------------------------------------------------


def test_coverage_before_reports_exactly_what_is_missing(db_session):
    _outage_world(db_session)
    cov = window_coverage(db_session, START, END, source=SRC)
    assert not cov.complete and (cov.competitive_games, cov.stored, cov.player_logs) == (2, 0, 0)
    assert cov.problems == [
        "2026-10-21 g21: status scheduled (schedule not refreshed since the game)",
        "2026-10-23 g23: status scheduled (schedule not refreshed since the game)",
        "2026-10-24: schedule never fetched",
    ]
    assert cov.lines[0] == "window 2026-10-20 to 2026-10-24: INCOMPLETE"


def test_catch_up_fills_only_the_window_and_verifies_it(db_session):
    stats = _outage_world(db_session)
    result = run_catch_up(db_session, stats, START, END, source=SRC, lookback_days=LOOKBACK, today=TODAY, now=NOW)
    assert stats.calls == [f"games:2026-10-{d}" for d in range(20, 25)] + ["box:g21", "box:g23", "box:g24"]
    assert result.box_scores.games_ingested == 3 and result.box_scores.logs_created == 6 and not result.box_scores.games_failed
    assert result.coverage.complete and (result.coverage.competitive_games, result.coverage.stored, result.coverage.player_logs) == (3, 3, 6)
    assert {g.source_game_id: (g.status, g.box_score_state) for g in db_session.scalars(select(NbaGame))} == {
        "g21": ("final", "ingested"), "g23": ("final", "ingested"), "g24": ("final", "ingested"),
    }


def test_timestamps_are_the_real_observation_time_and_history_is_kept(db_session):
    stats = _outage_world(db_session)
    run_catch_up(db_session, stats, START, END, source=SRC, lookback_days=LOOKBACK, today=TODAY, now=NOW)
    observations = {
        (o.game.source_game_id, o.status): ensure_utc(o.observed_at)
        for o in db_session.scalars(select(NbaGameScheduleObservation))
    }
    # The 9 October "scheduled" observations are untouched; the result is observed now, not at tip-off.
    assert observations == {
        ("g21", "scheduled"): BEFORE_OUTAGE, ("g23", "scheduled"): BEFORE_OUTAGE,
        ("g21", "final"): NOW, ("g23", "final"): NOW, ("g24", "final"): NOW,
    }
    # No pregame confirmation is invented for a result fetched days later.
    assert db_session.scalar(select(func.count()).select_from(NbaEvidencePoll)) == 0
    for log in db_session.scalars(select(NbaPlayerGameLog)):
        assert ensure_utc(log.recorded_at) > ensure_utc(log.game.scheduled_start)


def test_only_schedule_and_box_score_tables_change(db_session):
    stats = _outage_world(db_session)
    before = _row_counts(db_session)
    run_catch_up(db_session, stats, START, END, source=SRC, lookback_days=LOOKBACK, today=TODAY, now=NOW)
    after = _row_counts(db_session)
    changed = {name for name in before if before[name] != after[name]}
    assert changed <= {"nba_games", "nba_game_schedule_observations", "nba_schedule_sync_dates", "nba_players", "nba_player_game_logs"}


def test_rerunning_is_a_no_op(db_session):
    stats = _outage_world(db_session)
    run_catch_up(db_session, stats, START, END, source=SRC, lookback_days=LOOKBACK, today=TODAY, now=NOW)
    before = _row_counts(db_session)
    stats.calls.clear()
    again = run_catch_up(db_session, stats, START, END, source=SRC, lookback_days=LOOKBACK, today=TODAY, now=NOW)
    assert stats.calls == [] and again.schedule.dates_skipped_settled == 5  # settled dates and stored games are not re-requested
    assert again.coverage.complete and _row_counts(db_session) == before


def test_a_stored_box_score_is_never_refetched_or_rewritten(db_session):
    stats = _outage_world(db_session)
    sync_schedule(db_session, stats, date(2026, 10, 21), date(2026, 10, 21), source=SRC, today=TODAY, now=NOW)
    g21 = db_session.scalars(select(NbaGame).filter_by(source_game_id="g21")).one()
    ingest_box_score(db_session, g21, replace(stats.get_box_score("g21"), fetched_at=datetime(2026, 10, 22, 8, tzinfo=timezone.utc)))
    stored = {log.player_id: (log.points, log.recorded_at) for log in db_session.scalars(select(NbaPlayerGameLog))}
    stats.calls.clear()
    run_catch_up(db_session, stats, START, END, source=SRC, lookback_days=LOOKBACK, today=TODAY, now=NOW)
    assert "box:g21" not in stats.calls
    assert {log.player_id: (log.points, log.recorded_at) for log in db_session.scalars(select(NbaPlayerGameLog)) if log.game_id == g21.id} == stored


def test_an_older_game_never_moves_a_player_back(db_session):
    stats = _outage_world(db_session)
    later_team = db_session.scalars(select(NbaTeam)).all()[-1]
    player = NbaPlayer(display_name="A", source=SRC, source_player_id="a", current_team_id=later_team.id, last_game_at=_tip(26))
    db_session.add(player)
    db_session.commit()
    run_catch_up(db_session, stats, START, END, source=SRC, lookback_days=LOOKBACK, today=TODAY, now=NOW)
    db_session.refresh(player)
    assert player.current_team_id == later_team.id and ensure_utc(player.last_game_at) == _tip(26)
    assert db_session.scalar(select(func.count()).select_from(NbaPlayerGameLog).where(NbaPlayerGameLog.player_id == player.id)) == 3


def test_preseason_postponed_and_bad_box_scores(db_session):
    stats = _outage_world(db_session)
    stats.games += [
        replace(_final("pre", 20), season_type="preseason"),
        _record("ppd", _tip(22), status="postponed"),
        _record("bad", _tip(22), status="final", home_score=101, away_score=90),  # player points will not add up
    ]
    result = run_catch_up(db_session, stats, START, END, source=SRC, lookback_days=LOOKBACK, today=TODAY, now=NOW)
    assert "box:pre" not in stats.calls and "box:ppd" not in stats.calls
    assert result.coverage.excluded == ["2026-10-22 ppd: postponed"]
    assert not result.coverage.complete and result.coverage.problems == ["2026-10-22 bad: final, box score rejected"]


# --- CLI ------------------------------------------------------------------


def _args(start=START, end=END, dry_run=False):
    return argparse.Namespace(start=start, end=end, dry_run=dry_run)


def test_cli_refuses_a_bad_window_with_exit_2(db_session, capsys):
    stats = _outage_world(db_session)
    assert _catch_up(db_session, _args(end=date(2026, 10, 27)), provider=stats, today=TODAY) == 2
    assert stats.calls == [] and "refused" in capsys.readouterr().out


def test_cli_dry_run_is_read_only(db_session, capsys):
    stats = _outage_world(db_session)
    before = _row_counts(db_session)
    assert _catch_up(db_session, _args(dry_run=True), provider=stats, today=TODAY) == 1  # incomplete
    assert stats.calls == [] and _row_counts(db_session) == before
    assert "MISSING 2026-10-24: schedule never fetched" in capsys.readouterr().out


def test_cli_catch_up_exits_0_once_complete(db_session, capsys):
    stats = _outage_world(db_session)
    assert _catch_up(db_session, _args(), provider=stats, today=TODAY) == 0
    assert "window 2026-10-20 to 2026-10-24: COMPLETE" in capsys.readouterr().out
    assert _catch_up(db_session, _args(dry_run=True), provider=stats, today=TODAY) == 0


# --- workflow -------------------------------------------------------------


def test_catch_up_workflow_safeguards():
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in wf and "schedule:" not in wf  # manual only
    assert '"$CONFIRM" != "catch-up-$FROM-$TO"' in wf
    assert "group: nba-current-season-catch-up" in wf and "group: nba-live-cycle" not in wf  # never blocks the live cycle
    assert "python -m app.nba.cli schema-status" in wf  # refuses a database not at the image's head
    assert 'python -m app.nba.cli catch-up --from "$FROM" --to "$TO" --dry-run' in wf
    assert 'python -m app.nba.cli catch-up --from "$FROM" --to "$TO"\n' in wf
    assert "ghcr.io/srat0029-ui/afl/backend:sha-*) ;;" in wf and "vars.NBA_LIVE_CYCLE_IMAGE" in wf
    for forbidden in ("alembic", "pg_restore", "psql", "DROP ", "TRUNCATE", "app.ingestion", "app.cli ", "outlooks", "run-live-cycle", "backfill --", "--refresh", "gh variable", "vars.NBA_LIVE_CYCLE_ENABLED"):
        assert forbidden not in wf, forbidden
