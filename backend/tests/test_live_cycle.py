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
    Bookmaker,
    LiveCycleRun,
    Match,
    MatchStatus,
    Player,
    PlayerMatchStat,
    PropMarketObservation,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.live_cycle import run_live_cycle
from app.providers.types import Fixture

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
    assert "refresh_team_odds" in step_names
    assert "refresh_weather" in step_names
    # No promoted disposal/goal model exists in this fresh test DB, so
    # regeneration recoverably fails - the cycle must still complete and
    # persist a run rather than crashing.
    assert run.overall_status in (RUN_OK, RUN_PARTIAL)


def test_team_odds_provider_not_configured_is_a_warning_not_a_failure(db_session, monkeypatch):
    _seed_scheduled_match(db_session)
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider())
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider())
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)

    run = run_live_cycle(db_session)

    team_odds_step = next(s for s in run.steps if s["step"] == "refresh_team_odds")
    assert team_odds_step["status"] == STEP_WARNING
    assert run.team_odds_quotes_added == 0


def test_weather_step_reports_skipped_when_match_has_no_venue(db_session, monkeypatch):
    """No venue was seeded on the test match, so refresh_weather_for_matches
    skips it before ever reaching the Open-Meteo provider - no HTTP call is
    made, matching the module's own network-free unit tests."""
    _seed_scheduled_match(db_session)
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider())
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider())
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)

    run = run_live_cycle(db_session)

    weather_step = next(s for s in run.steps if s["step"] == "refresh_weather")
    assert weather_step["status"] == STEP_SUCCESS
    assert run.weather_snapshots_added == 0


def test_team_odds_needs_refresh_gate(db_session):
    from app.models import Bookmaker, OddsQuote
    from app.player_modelling.live_cycle import _team_odds_needs_refresh
    from app.player_modelling.upcoming_features import UpcomingMatchTeams

    match = _seed_scheduled_match(db_session)
    upcoming = [UpcomingMatchTeams(
        match_id=match.id, home_team_id=match.home_team_id, away_team_id=match.away_team_id, venue_id=None,
        scheduled_start=match.scheduled_start, season_year=2026, round_number=1, is_final=False,
    )]

    # No automated team-odds quote at all yet -> needs a refresh.
    assert _team_odds_needs_refresh(db_session, upcoming) is True

    bookmaker = Bookmaker(name="SportsBet")
    db_session.add(bookmaker)
    db_session.flush()
    db_session.add(OddsQuote(
        match_id=match.id, bookmaker_id=bookmaker.id, market_type="h2h", selection="Collingwood",
        line_value=None, price_decimal=1.9, recorded_at=datetime.now(timezone.utc), source="the_odds_api", is_closing_line=False,
    ))
    db_session.commit()

    # A quote fetched moments ago, for a match still days from bounce, is
    # within the match-time-aware interval -> no refresh needed yet.
    assert _team_odds_needs_refresh(db_session, upcoming) is False


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


def _seed_in_progress_match_with_pending_observation(db):
    """Reproduces the real production scenario this cycle must recover
    from: a match stuck IN_PROGRESS (the Squiggle `complete=0` bug meant it
    could never be refreshed), player stats already available (ingested
    separately, or from a prior attempt), and one prop observation still
    waiting to be settled."""
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=24)
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW - timedelta(days=5), status=MatchStatus.IN_PROGRESS,
        external_ids={"squiggle": "70001"},
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db.add_all([player, bookmaker])
    db.flush()
    # A genuinely-upcoming match must also exist: step 2 of the cycle
    # (identify_upcoming_round) only looks for SCHEDULED matches and
    # returns early — before settlement — if none exist. Any live AFL
    # season always has one, so this mirrors realistic state; it's a
    # separate match, untouched by the fixture refresh below.
    next_round = Round(season_id=season.id, round_number=25)
    db.add(next_round)
    db.flush()
    db.add(Match(
        sport_id=sport.id, season_id=season.id, round_id=next_round.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    ))
    stat = PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables",
        recorded_at=NOW - timedelta(days=4), disposals=32, goals=1,
    )
    observation = PropMarketObservation(
        quote_id=1, match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id,
        market_type="player_disposals", line_type="over_under", threshold=29.5, source="the_odds_api",
        offered_odds=1.9, observed_at=NOW - timedelta(days=5), raw_implied_probability=0.526,
        devigged_probability=None, overround_removed=False,
        model_probability=0.55, model_fair_odds=1.82, predicted_mean=28.0,
        model_name="disposals_nb", model_version="v1", data_cutoff=NOW - timedelta(days=5),
        confidence_tier="moderate_confidence", selection_status_at_observation="confirmed_selected",
        is_confirmed_at_observation=True, difference_pp=0.024, expected_value=0.045,
    )
    db.add_all([stat, observation])
    db.commit()
    return match, observation


def test_stuck_in_progress_match_completes_and_settles_via_delayed_fixture_refresh(db_session, monkeypatch):
    """End-to-end regression for the real bug this cycle was built to fix:
    a fixture refresh that finally reports a long-stuck IN_PROGRESS match
    as completed must, in the SAME cycle, flip the match to COMPLETED and
    settle whichever observations were only waiting on that."""
    match, observation = _seed_in_progress_match_with_pending_observation(db_session)
    completed_fixture = Fixture(
        external_id="70001", sport_code="AFL", season_year=2026, round_number=24,
        home_team="Collingwood", away_team="Carlton", scheduled_start=match.scheduled_start,
        status="completed", home_score=86, away_score=81,
    )
    monkeypatch.setattr(live_cycle_module, "SquiggleFixtureProvider", lambda: FakeFixtureProvider(fixtures=[completed_fixture]))
    monkeypatch.setattr(live_cycle_module, "AFLTablesPlayerStatsProvider", lambda: FakePlayerStatsProvider())
    monkeypatch.setattr(live_cycle_module, "TheOddsApiProvider", FakeOddsProvider)

    run = run_live_cycle(db_session)

    db_session.refresh(match)
    db_session.refresh(observation)
    assert match.status == MatchStatus.COMPLETED
    assert match.home_score == 86
    assert observation.settled_at is not None
    assert observation.market_result == "won"  # 32 disposals clears the 29.5 line
    assert observation.actual_stat_value == 32.0
    assert run.observations_settled == 1

    # Idempotent rerun: the same fixture/result data must not re-settle or
    # otherwise double-count anything on a second pass.
    second_run = run_live_cycle(db_session)
    db_session.refresh(match)
    assert match.status == MatchStatus.COMPLETED
    assert second_run.observations_settled == 0
