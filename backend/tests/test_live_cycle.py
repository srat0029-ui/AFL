"""Tests for run_live_cycle orchestration (Sections 1-3, 20 of the
live-operations stage brief): step ordering/reuse, failure classification
(warning/recoverable/blocking), and the persisted LiveCycleRun summary.
Every external provider is monkeypatched at its import site inside
live_cycle.py — these tests never make a real network call."""

from datetime import datetime, timedelta, timezone

import app.player_modelling.live_cycle as live_cycle_module
from app.models import (
    RUN_BLOCKED,
    RUN_OK,
    RUN_PARTIAL,
    STEP_RECOVERABLE_FAILURE,
    STEP_SUCCESS,
    STEP_WARNING,
    LiveCycleRun,
    Match,
    MatchStatus,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.live_cycle import run_live_cycle

NOW = datetime.now(timezone.utc)


class FakeFixtureProvider:
    def __init__(self, *, fixtures=None, raises=False):
        self._fixtures = fixtures or []
        self._raises = raises

    def get_upcoming_fixtures(self, sport_code):
        if self._raises:
            raise RuntimeError("Squiggle temporarily unavailable")
        return self._fixtures


class FakePlayerStatsProvider:
    def __init__(self, *, raises=False):
        self._raises = raises

    def get_team_season_player_stats(self, sport_code, season_year, team_name):
        if self._raises:
            raise RuntimeError("AFL Tables temporarily blocking scraper traffic")
        return []


class FakeOddsProvider:
    """Constructed with the same (api_key=...) signature as TheOddsApiProvider."""

    def __init__(self, api_key=None):
        self.is_available = False  # no key configured - the common real case in dev/test


def _seed_scheduled_match(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=3), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match


def test_full_cycle_persists_a_run_with_every_step(db_session, monkeypatch):
    _seed_scheduled_match(db_session)
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider())
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider())
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)

    run = run_live_cycle(db_session)

    assert isinstance(run, LiveCycleRun)
    assert run.id is not None
    step_names = [s["step"] for s in run.steps]
    assert "refresh_fixtures" in step_names
    assert "identify_upcoming_round" in step_names
    assert "update_completed_player_stats" in step_names
    assert "settle_props" in step_names
    assert "regenerate_projections" in step_names
    assert "refresh_prop_odds" in step_names
    # No promoted disposal/goal model exists in this fresh test DB, so
    # regeneration recoverably fails - the cycle must still complete and
    # persist a run rather than crashing.
    assert run.overall_status in (RUN_OK, RUN_PARTIAL)


def test_odds_provider_not_configured_is_a_warning_not_a_failure(db_session, monkeypatch):
    _seed_scheduled_match(db_session)
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider())
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider())
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)

    run = run_live_cycle(db_session)

    odds_step = next(s for s in run.steps if s["step"] == "refresh_prop_odds")
    assert odds_step["status"] == STEP_WARNING


def test_fixture_refresh_failure_is_recoverable_when_matches_already_exist(db_session, monkeypatch):
    """Section 2's own example: fixture refresh fails, but odds refresh
    (and everything else) still proceeds normally using already-ingested
    fixtures - this must be RECOVERABLE, not blocking."""
    _seed_scheduled_match(db_session)
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider(raises=True))
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider())
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)

    run = run_live_cycle(db_session)

    fixtures_step = next(s for s in run.steps if s["step"] == "refresh_fixtures")
    assert fixtures_step["status"] == STEP_RECOVERABLE_FAILURE
    assert run.overall_status == RUN_PARTIAL
    # The cycle still found and processed the existing match.
    identify_step = next(s for s in run.steps if s["step"] == "identify_upcoming_round")
    assert identify_step["status"] == STEP_SUCCESS


def test_no_fixtures_at_all_and_refresh_failing_is_blocking(db_session, monkeypatch):
    """Nothing was ever ingested AND the fixture refresh itself failed -
    genuinely nothing meaningful can be done this cycle."""
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider(raises=True))
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider())
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)

    run = run_live_cycle(db_session)

    assert run.overall_status == RUN_BLOCKED


def _add_completed_match_without_stats(db, sport_id, season_id, home_id, away_id):
    round_ = Round(season_id=season_id, round_number=2)
    db.add(round_)
    db.flush()
    match = Match(
        sport_id=sport_id, season_id=season_id, round_id=round_.id, home_team_id=home_id, away_team_id=away_id,
        scheduled_start=NOW - timedelta(days=1), status=MatchStatus.COMPLETED,
    )
    db.add(match)
    db.commit()
    return match


def test_player_stat_source_failure_is_recoverable_and_other_steps_still_run(db_session, monkeypatch):
    """Section 2's canonical example: player-stat source temporarily
    unavailable while odds refresh still proceeds."""
    scheduled = _seed_scheduled_match(db_session)
    # A separate COMPLETED match with zero PlayerMatchStat rows triggers the
    # player-stat-update step to actually attempt a fetch, while the
    # SCHEDULED match above keeps identify_upcoming_round succeeding.
    _add_completed_match_without_stats(
        db_session, scheduled.sport_id, scheduled.season_id, scheduled.home_team_id, scheduled.away_team_id
    )

    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider())
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider(raises=True))
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)

    run = run_live_cycle(db_session)

    player_stats_step = next(s for s in run.steps if s["step"] == "update_completed_player_stats")
    assert player_stats_step["status"] == STEP_RECOVERABLE_FAILURE
    assert run.overall_status == RUN_PARTIAL
    # A subsequent step (odds refresh) still ran despite the earlier failure.
    assert any(s["step"] == "refresh_prop_odds" for s in run.steps)


def test_player_stat_update_skipped_cleanly_when_no_completed_matches_are_missing_stats(db_session, monkeypatch):
    _seed_scheduled_match(db_session)  # still SCHEDULED - nothing to update
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider())
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider())
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)

    run = run_live_cycle(db_session)

    step = next(s for s in run.steps if s["step"] == "update_completed_player_stats")
    assert step["status"] == STEP_SUCCESS


def test_run_exit_code_matches_overall_status():
    from app.player_modelling.live_cycle import LiveCycleReport

    ok_report = LiveCycleReport()
    ok_report.add("x", STEP_SUCCESS, "fine")
    assert ok_report.exit_code == 0

    partial_report = LiveCycleReport()
    partial_report.add("x", STEP_RECOVERABLE_FAILURE, "oops")
    assert partial_report.exit_code == 1

    from app.models import STEP_BLOCKING_FAILURE

    blocked_report = LiveCycleReport()
    blocked_report.add("x", STEP_BLOCKING_FAILURE, "nothing works")
    assert blocked_report.exit_code == 2


def test_settlement_runs_before_new_data_collection_in_step_order(db_session, monkeypatch):
    """Section 14: settlement must happen before new-data collection."""
    _seed_scheduled_match(db_session)
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider())
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider())
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)

    run = run_live_cycle(db_session)

    step_names = [s["step"] for s in run.steps]
    assert step_names.index("settle_props") < step_names.index("refresh_prop_odds")
