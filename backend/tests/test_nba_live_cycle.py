"""The NBA live cycle as the hosted schedule will drive it: adaptive lineup
polling, due/not-due decisions, one-run-at-a-time locking, failure
classification and exit codes, and the evidence-health monitor. Providers
are in-memory fakes - never a network call."""

import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.config import Settings
from app.models.nba import (
    NbaEvidencePoll,
    NbaGame,
    NbaGameLineupObservation,
    NbaGameScheduleObservation,
    NbaLiveCycleRun,
    NbaPlayerAvailabilityReport,
    NbaPlayerGameLog,
    NbaTeamObservation,
)
from app.nba.asof import game_schedule_known_at
from app.nba.live_cycle import (
    ADVISORY_LOCK_KEY,
    CycleAlreadyRunning,
    NbaPollingPolicy,
    games_needing_lineup_poll,
    parse_lineup_tiers,
    run_live_cycle,
    run_status,
)
from app.migration_status import MigrationStatus, migration_status
from app.nba.monitoring import evidence_health
from app.nba.operations import EXIT_EVIDENCE_STALE, EXIT_INTERNAL_ERROR, EXIT_OK, EXIT_SCHEMA_NOT_CURRENT, run_cycle_command
from app.providers.nba.espn import EspnNbaError
from app.providers.nba.evidence_types import NbaDepthChart, NbaRosterPlayer, NbaTeamRoster
from app.providers.nba.types import NbaBoxScore, NbaGameRecord, NbaPlayerBoxLine, NbaTeamRecord
from tests.test_nba_live_evidence import SRC, _feed, _item, _lineup

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "nba-live-cycle.yml"
NOW = datetime(2026, 10, 20, 20, 0, tzinfo=timezone.utc)
SOON_TIP = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)  # 3 hours after NOW
POLICY = NbaPollingPolicy()


# --- fakes -----------------------------------------------------------------


class Killed(BaseException):  # not an Exception: nothing in the cycle may swallow it
    pass


class FakeStats:
    def __init__(self, games: list[NbaGameRecord], fail_box: bool = False):
        self.games, self.fail_box = games, fail_box
        self.calls: list[str] = []

    def get_teams(self):
        return [NbaTeamRecord(source=SRC, source_team_id=str(i), name=f"Team {i}", abbreviation=f"T{i}") for i in (1, 2, 3)]

    def get_games(self, on, *, team_ids=None):
        self.calls.append(f"games:{on}")
        return [g for g in self.games if g.game_date == on]

    def get_box_score(self, source_game_id):
        self.calls.append(f"box:{source_game_id}")
        if self.fail_box:
            raise EspnNbaError("summary down")
        lines = [
            NbaPlayerBoxLine(source_player_id="a", player_name="A", position="G", source_team_id="1", did_not_play=False, started=True, minutes=30.0, points=100),
            NbaPlayerBoxLine(source_player_id="x", player_name="X", position="G", source_team_id="2", did_not_play=False, started=True, minutes=30.0, points=90),
        ]
        return NbaBoxScore(source=SRC, source_game_id=source_game_id, status="final", fetched_at=datetime.now(timezone.utc), lines=lines)


class FakeEvidence:
    def __init__(self, now: datetime, *, fail: set[str] | None = None, die_on_team: str | None = None, crash: set[str] | None = None, items=None, starters=None):
        self.now, self.fail, self.die_on_team, self.crash = now, fail or set(), die_on_team, crash or set()
        self.items = items if items is not None else [_item("a", "Day-To-Day", name="A")]
        self.starters = starters
        self.calls: list[str] = []

    def get_injuries(self):
        self.calls.append("injuries")
        if "injuries" in self.fail:
            raise EspnNbaError("injuries down")
        if "injuries" in self.crash:
            raise ZeroDivisionError("a bug")
        return _feed(self.items, self.now)

    def get_team_roster(self, source_team_id):
        self.calls.append(f"roster:{source_team_id}")
        if self.die_on_team == source_team_id:
            raise Killed()
        if "roster" in self.fail:
            raise EspnNbaError("roster down")
        return NbaTeamRoster(source=SRC, source_team_id=source_team_id, fetched_at=self.now, source_timestamp=self.now, players=[NbaRosterPlayer("a", "A", "G", None, "Active")])

    def get_team_depth_chart(self, source_team_id, season_start_year):
        self.calls.append(f"depth:{source_team_id}:{season_start_year}")
        return NbaDepthChart(source=SRC, source_team_id=source_team_id, fetched_at=self.now, positions={"pg": ["a"]})

    def get_game_lineups(self, source_game_id, source_team_ids):
        self.calls.append(f"lineups:{source_game_id}")
        if "lineups" in self.fail:
            raise EspnNbaError("lineups down")
        return [_lineup(self.now, starters=self.starters, team=tid, game=source_game_id) for tid in source_team_ids]


def _record(game_id, tipoff, status="scheduled", **scores) -> NbaGameRecord:
    return NbaGameRecord(
        source=SRC, source_game_id=game_id, season_start_year=2026, season_type="regular", game_date=tipoff.date(), scheduled_start=tipoff, status=status,
        home_source_team_id="1", away_source_team_id="2", home_team_name="Team 1", away_team_name="Team 2", source_status=f"STATUS_{status.upper()}", **scores,
    )


def _games():
    return [_record("soon", SOON_TIP), _record("far", datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc))]


def _steps(run: NbaLiveCycleRun) -> dict[str, str]:
    return {s["step"]: s["status"] for s in run.steps}


@contextmanager
def _another_cycle_running(db):
    """What "another cycle is running" looks like to this one: on PostgreSQL
    another connection holding the advisory lock, elsewhere a recent run
    still marked in progress."""
    if db.get_bind().dialect.name == "postgresql":
        engine = create_engine(os.environ["TEST_DATABASE_URL"])
        with engine.connect() as holder:
            holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY})
            holder.commit()
            try:
                yield
            finally:
                holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
                holder.commit()
        engine.dispose()
    else:
        db.add(NbaLiveCycleRun(started_at=NOW - timedelta(minutes=5), status="in_progress", steps=[]))
        db.commit()
        yield


def _cycle(db, now, *, stats=None, evidence=None, policy=POLICY, force=False):
    return run_live_cycle(db, stats or FakeStats(_games()), evidence or FakeEvidence(now), source=SRC, policy=policy, now=now, force=force)


# --- adaptive lineup polling -----------------------------------------------


def test_lineup_tiers_are_parsed_from_settings_text():
    tiers = parse_lineup_tiers("1:15, 24:60,4:30")
    assert tiers == ((timedelta(hours=24), timedelta(minutes=60)), (timedelta(hours=4), timedelta(minutes=30)), (timedelta(hours=1), timedelta(minutes=15)))
    for bad in ("", "24", "24:0", "-1:10", "a:b", "4:30,4:15"):
        with pytest.raises(ValueError):
            parse_lineup_tiers(bad)


@pytest.mark.parametrize(
    "hours_to_tip, expected_minutes",
    [(30, None), (24.01, None), (24, 60), (10, 60), (4.01, 60), (4, 30), (2, 30), (1.01, 30), (1, 15), (0.25, 15), (0, None), (-0.5, None)],
)
def test_lineup_interval_tightens_as_tipoff_approaches(hours_to_tip, expected_minutes):
    interval = POLICY.lineup_interval(timedelta(hours=hours_to_tip))
    assert interval == (None if expected_minutes is None else timedelta(minutes=expected_minutes))


def test_policy_reads_every_interval_from_settings():
    settings = Settings(
        nba_poll_availability_minutes=5, nba_poll_schedule_minutes=7, nba_poll_team_rosters_minutes=11, nba_poll_depth_charts_minutes=13,
        nba_poll_box_scores_minutes=17, nba_lineup_poll_tiers="12:20,2:10", nba_live_cycle_stale_after_minutes=25,
    )
    policy = NbaPollingPolicy.from_settings(settings)
    assert (policy.availability_interval, policy.schedule_interval, policy.roster_interval, policy.depth_chart_interval, policy.box_score_interval) == (
        timedelta(minutes=5), timedelta(minutes=7), timedelta(minutes=11), timedelta(minutes=13), timedelta(minutes=17)
    )
    assert policy.lineup_horizon == timedelta(hours=12) and policy.lineup_interval(timedelta(hours=1)) == timedelta(minutes=10)
    assert policy.stale_run_after == timedelta(minutes=25)


def test_only_games_inside_the_horizon_and_due_by_their_tier_are_polled(db_session):
    games = [
        _record("t-30m", NOW + timedelta(minutes=30)),
        _record("t-3h", NOW + timedelta(hours=3)),
        _record("t-10h", NOW + timedelta(hours=10)),
        _record("t-30h", NOW + timedelta(hours=30)),
        _record("tipped", NOW - timedelta(minutes=5)),
    ]
    run = _cycle(db_session, NOW, stats=FakeStats(games))
    polled = {o.game.source_game_id for o in db_session.query(NbaGameLineupObservation)}
    assert polled == {"t-30m", "t-3h", "t-10h"} and _steps(run)["game_lineups"] == "ok"

    # 20 minutes later only the game in its final hour (15-minute tier) is due again.
    later = NOW + timedelta(minutes=20)
    assert [g.source_game_id for g, _ in games_needing_lineup_poll(db_session, SRC, POLICY, later)] == ["t-30m"]
    # 35 minutes later the 3-hour game (30-minute tier) is due too; the 10-hour one (hourly) is not.
    assert [g.source_game_id for g, _ in games_needing_lineup_poll(db_session, SRC, POLICY, NOW + timedelta(minutes=35))] == ["t-3h"]  # t-30m has tipped off by then
    assert {g.source_game_id for g, _ in games_needing_lineup_poll(db_session, SRC, POLICY, NOW + timedelta(minutes=61))} == {"t-3h", "t-10h"}


def test_lineup_polling_stops_at_tipoff_and_for_games_no_longer_scheduled(db_session):
    games = [_record("soon", SOON_TIP)]
    _cycle(db_session, NOW, stats=FakeStats(games))
    assert games_needing_lineup_poll(db_session, SRC, POLICY, SOON_TIP) == []
    assert games_needing_lineup_poll(db_session, SRC, POLICY, SOON_TIP + timedelta(minutes=1)) == []

    db_session.query(NbaGame).filter_by(source_game_id="soon").one().status = "postponed"
    db_session.commit()
    assert games_needing_lineup_poll(db_session, SRC, POLICY, NOW + timedelta(hours=2), force=True) == []


def test_the_moment_starters_first_appear_is_captured(db_session):
    _cycle(db_session, NOW)
    near_tip = SOON_TIP - timedelta(minutes=40)
    _cycle(db_session, near_tip, evidence=FakeEvidence(near_tip, starters=["a", "b", "c", "d", "e"]))
    rows = db_session.query(NbaGameLineupObservation).filter_by(team_id=db_session.query(NbaGame).filter_by(source_game_id="soon").one().home_team_id).order_by(NbaGameLineupObservation.id).all()
    assert [(r.has_starter_field, r.starters_flagged) for r in rows] == [(False, 0), (True, 5)]
    assert [round((r.tipoff_at_observation.replace(tzinfo=timezone.utc) - r.observed_at.replace(tzinfo=timezone.utc)).total_seconds() / 60) for r in rows] == [180, 40]


# --- due / not due ---------------------------------------------------------


def test_first_cycle_collects_everything_that_is_due(db_session):
    stats, evidence = FakeStats(_games()), FakeEvidence(NOW)
    run = _cycle(db_session, NOW, stats=stats, evidence=evidence)
    assert run.status == "ok" and run.finished_at is not None
    assert _steps(run) == {s: "ok" for s in ("schedule", "availability", "team_rosters", "team_depth_charts", "game_lineups", "box_scores")}
    assert db_session.query(NbaTeamObservation).count() == 6
    assert [c for c in evidence.calls if c.startswith("lineups")] == ["lineups:soon"]  # "far" is outside the 24-hour horizon
    assert "depth:1:2026" in evidence.calls


def test_a_cycle_woken_again_immediately_makes_no_requests_and_writes_nothing(db_session):
    _cycle(db_session, NOW)
    models = (NbaPlayerAvailabilityReport, NbaTeamObservation, NbaGameLineupObservation, NbaGameScheduleObservation, NbaEvidencePoll)
    counts = [db_session.query(m).count() for m in models]

    stats, evidence = FakeStats(_games()), FakeEvidence(NOW + timedelta(minutes=5))
    run = _cycle(db_session, NOW + timedelta(minutes=5), stats=stats, evidence=evidence)
    assert set(_steps(run).values()) == {"skipped"} and run.status == "ok"
    assert stats.calls == [] and evidence.calls == []
    assert [db_session.query(m).count() for m in models] == counts


def test_each_source_becomes_due_on_its_own_interval(db_session):
    _cycle(db_session, NOW)
    later = NOW + timedelta(minutes=35)  # past the 30-minute injury and lineup intervals only
    evidence = FakeEvidence(later)
    run = _cycle(db_session, later, evidence=evidence)
    assert {k for k, v in _steps(run).items() if v == "ok"} == {"availability", "game_lineups"}
    assert sorted(evidence.calls) == ["injuries", "lineups:soon"]

    much_later = NOW + timedelta(hours=3, minutes=1)  # past schedule (3h) and box scores (1h); rosters and depth charts are daily
    run = _cycle(db_session, much_later, evidence=FakeEvidence(much_later))
    assert {k for k, v in _steps(run).items() if v == "ok"} == {"schedule", "availability", "box_scores"}


def test_force_ignores_every_interval(db_session):
    _cycle(db_session, NOW)
    run = _cycle(db_session, NOW + timedelta(minutes=1), evidence=FakeEvidence(NOW + timedelta(minutes=1)), force=True)
    assert set(_steps(run).values()) == {"ok"}
    assert db_session.query(NbaPlayerAvailabilityReport).count() == 1 and db_session.query(NbaTeamObservation).count() == 6  # nothing duplicated


# --- one run at a time -----------------------------------------------------


def test_a_second_cycle_refuses_while_one_is_in_progress(db_session):
    evidence = FakeEvidence(NOW)
    with _another_cycle_running(db_session):
        runs_before = db_session.query(NbaLiveCycleRun).count()
        with pytest.raises(CycleAlreadyRunning):
            _cycle(db_session, NOW, evidence=evidence)
        assert evidence.calls == [] and db_session.query(NbaLiveCycleRun).count() == runs_before


@pytest.mark.skipif((os.environ.get("TEST_DATABASE_URL") or "").startswith("postgresql"), reason="on PostgreSQL the advisory lock decides; covered by the postgres tests")
def test_a_dead_run_does_not_block_forever(db_session):
    db_session.add(NbaLiveCycleRun(started_at=NOW - POLICY.stale_run_after - timedelta(minutes=1), status="in_progress", steps=[]))
    db_session.commit()
    run = _cycle(db_session, NOW)
    assert run.status == "ok"
    assert [r.status for r in db_session.query(NbaLiveCycleRun).order_by(NbaLiveCycleRun.id)] == ["interrupted", "ok"]


@pytest.mark.skipif(not (os.environ.get("TEST_DATABASE_URL") or "").startswith("postgresql"), reason="advisory locks need a real PostgreSQL")
def test_postgres_advisory_lock_blocks_a_concurrent_cycle_and_is_released_after(db_session):
    other = create_engine(os.environ["TEST_DATABASE_URL"])
    with other.connect() as holder:
        assert holder.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar() is True
        holder.commit()
        evidence = FakeEvidence(NOW)
        with pytest.raises(CycleAlreadyRunning):
            _cycle(db_session, NOW, evidence=evidence)
        assert evidence.calls == [] and db_session.query(NbaLiveCycleRun).count() == 0
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
        holder.commit()
    other.dispose()
    assert _cycle(db_session, NOW).status == "ok"
    # Released afterwards: a fresh connection can take it.
    with create_engine(os.environ["TEST_DATABASE_URL"]).connect() as check:
        assert check.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar() is True
        check.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})


@pytest.mark.skipif(not (os.environ.get("TEST_DATABASE_URL") or "").startswith("postgresql"), reason="advisory locks need a real PostgreSQL")
def test_postgres_holding_the_lock_proves_open_runs_are_dead(db_session):
    db_session.add(NbaLiveCycleRun(started_at=NOW - timedelta(minutes=2), status="in_progress", steps=[]))
    db_session.commit()
    assert _cycle(db_session, NOW).status == "ok"  # no 40-minute wait: nobody else holds the lock
    assert [r.status for r in db_session.query(NbaLiveCycleRun).order_by(NbaLiveCycleRun.id)] == ["interrupted", "ok"]


# --- failures --------------------------------------------------------------


def test_a_source_failure_records_nothing_for_that_step_and_leaves_earlier_evidence_intact(db_session):
    _cycle(db_session, NOW)
    before = [(r.id, r.source_status, r.observed_at) for r in db_session.query(NbaPlayerAvailabilityReport)]
    later = NOW + timedelta(minutes=31)
    run = _cycle(db_session, later, evidence=FakeEvidence(later, fail={"injuries"}))

    step = next(s for s in run.steps if s["step"] == "availability")
    assert (step["status"], step["error_kind"]) == ("failed", "source") and run.status == "partial"
    assert [(r.id, r.source_status, r.observed_at) for r in db_session.query(NbaPlayerAvailabilityReport)] == before
    assert db_session.query(NbaEvidencePoll).filter_by(kind="availability").count() == 1  # a failed read is not a poll


def test_a_failed_step_is_retried_on_the_next_wakeup_not_within_the_run(db_session):
    evidence = FakeEvidence(NOW, fail={"injuries"})
    _cycle(db_session, NOW, evidence=evidence)
    assert evidence.calls.count("injuries") == 1  # no retry loop inside a run
    retry = _cycle(db_session, NOW + timedelta(minutes=1), evidence=FakeEvidence(NOW + timedelta(minutes=1)))
    assert _steps(retry)["availability"] == "ok" and db_session.query(NbaPlayerAvailabilityReport).count() == 1


def test_an_internal_error_is_classified_separately_and_fails_the_run(db_session):
    run = _cycle(db_session, NOW, evidence=FakeEvidence(NOW, crash={"injuries"}))
    step = next(s for s in run.steps if s["step"] == "availability")
    assert (step["status"], step["error_kind"]) == ("failed", "internal") and "ZeroDivisionError" in step["detail"]
    assert run.status == "failed"
    assert _steps(run)["team_rosters"] == "ok"  # the other steps still ran


def test_a_partly_failing_team_sweep_keeps_what_succeeded(db_session):
    run = _cycle(db_session, NOW, evidence=FakeEvidence(NOW, fail={"roster"}))
    roster = next(s for s in run.steps if s["step"] == "team_rosters")
    assert (roster["status"], roster["error_kind"]) == ("failed", "source")  # every team failed
    assert _steps(run)["team_depth_charts"] == "ok" and run.status == "partial"


@pytest.mark.parametrize(
    "statuses, expected",
    [
        ([("ok", None), ("skipped", None)], "ok"),
        ([("ok", None), ("failed", "source")], "partial"),
        ([("skipped", None), ("failed", "source")], "failed"),  # everything that was due failed
        ([("ok", None), ("failed", "internal")], "failed"),
        ([("skipped", None), ("skipped", None)], "ok"),
    ],
)
def test_run_status_classification(statuses, expected):
    assert run_status([{"status": s, "error_kind": k} for s, k in statuses]) == expected


def test_an_interrupted_cycle_is_recovered_by_running_it_again(db_session):
    with pytest.raises(Killed):
        _cycle(db_session, NOW, evidence=FakeEvidence(NOW, die_on_team="2"))
    db_session.rollback()
    killed = db_session.query(NbaLiveCycleRun).one()
    assert killed.status == "in_progress" and [s["step"] for s in killed.steps] == ["schedule", "availability"]
    assert db_session.query(NbaTeamObservation).count() == 1  # team 1's roster was committed before the kill

    later = NOW + POLICY.stale_run_after + timedelta(minutes=1)
    run = _cycle(db_session, later, evidence=FakeEvidence(later), policy=replace(POLICY, roster_interval=timedelta(minutes=1)))
    db_session.refresh(killed)
    assert (killed.status, run.status) == ("interrupted", "ok")
    assert db_session.query(NbaTeamObservation).count() == 6  # finished, team 1 not duplicated
    assert db_session.query(NbaPlayerAvailabilityReport).count() == 1 and db_session.query(NbaGame).count() == 2


def test_box_scores_are_ingested_once_games_are_final(db_session):
    games = _games()
    _cycle(db_session, NOW, stats=FakeStats(games))
    after = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)
    games[0] = _record("soon", SOON_TIP, status="final", home_score=100, away_score=90)
    run = _cycle(db_session, after, stats=FakeStats(games))
    assert _steps(run)["box_scores"] == "ok"
    game = db_session.query(NbaGame).filter_by(source_game_id="soon").one()
    assert (game.status, game.box_score_state) == ("final", "ingested") and db_session.query(NbaPlayerGameLog).count() == 2
    assert [o.status for o in db_session.query(NbaGameScheduleObservation).filter_by(game_id=game.id).order_by(NbaGameScheduleObservation.id)] == ["scheduled", "final"]

    again = FakeStats(games)
    assert _steps(_cycle(db_session, after + timedelta(hours=2), stats=again))["box_scores"] == "ok"
    assert not [c for c in again.calls if c.startswith("box")]  # already ingested: not fetched again


def test_a_postponement_is_history_and_stops_lineup_polling(db_session):
    games = _games()
    _cycle(db_session, NOW, stats=FakeStats(games))
    later = NOW + timedelta(hours=3, minutes=5)
    games[0] = _record("soon", SOON_TIP + timedelta(days=2), status="postponed")
    evidence = FakeEvidence(later)
    _cycle(db_session, later, stats=FakeStats(games), evidence=evidence)
    game = db_session.query(NbaGame).filter_by(source_game_id="soon").one()
    assert game.status == "postponed" and not [c for c in evidence.calls if c.startswith("lineups")]
    assert game_schedule_known_at(db_session, game.id, NOW + timedelta(hours=1)).status == "scheduled"
    assert game_schedule_known_at(db_session, game.id, later).status == "postponed"


# --- exit codes for the hosted job -----------------------------------------


def _command(db, now, **kwargs):
    out = StringIO()
    code = run_cycle_command(db, kwargs.pop("stats", FakeStats(_games())), kwargs.pop("evidence", FakeEvidence(now)), source=SRC, now=now,
                             settings=Settings(), check_schema_first=kwargs.pop("check_schema_first", False), out=out, **kwargs)
    return code, out.getvalue()


def test_a_clean_run_exits_zero(db_session):
    code, output = _command(db_session, NOW)
    assert code == EXIT_OK and "evidence health: OK" in output


def test_a_transient_source_failure_does_not_fail_the_job(db_session, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _command(db_session, NOW)
    later = NOW + timedelta(minutes=31)
    code, output = _command(db_session, later, evidence=FakeEvidence(later, fail={"injuries"}))
    assert code == EXIT_OK
    assert "::warning::availability: injuries down" in output


def test_sustained_source_failure_fails_the_job_through_staleness(db_session, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _command(db_session, NOW)
    later = NOW + timedelta(minutes=95)  # past 3 x the 30-minute injury interval
    code, output = _command(db_session, later, evidence=FakeEvidence(later, fail={"injuries"}))
    assert code == EXIT_EVIDENCE_STALE
    assert "::error::Injury / availability feed: last successful poll 95 minutes ago" in output


def test_an_internal_error_fails_the_job(db_session):
    code, output = _command(db_session, NOW, evidence=FakeEvidence(NOW, crash={"injuries"}))
    assert code == EXIT_INTERNAL_ERROR and "(internal)" in output


def test_a_database_that_is_not_migrated_is_refused_before_anything_runs(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.nba.operations.migration_status",
        lambda db: MigrationStatus(database_revision="75715f635e7a", code_head="90343a82a7c8", pending=[("04b59a8a329c", "x")], is_current=False),
    )
    evidence = FakeEvidence(NOW)
    code, output = _command(db_session, NOW, evidence=evidence, check_schema_first=True)
    assert code == EXIT_SCHEMA_NOT_CURRENT and "migrations are never applied automatically" in output
    assert evidence.calls == [] and db_session.query(NbaLiveCycleRun).count() == 0


def test_a_locked_cycle_exits_zero_without_doing_anything(db_session):
    with _another_cycle_running(db_session):
        code, output = _command(db_session, NOW)
    assert code == EXIT_OK and output.startswith("skipped: another NBA live cycle")


def test_migration_status_reports_pending_revisions_in_order(db_session, monkeypatch):
    status = migration_status(db_session)
    assert status.code_head == "72256bdebf34"
    if status.database_revision is None:  # a create_all test database has no revision at all
        assert not status.is_current and [rev for rev, _ in status.pending][-7:] == ["04b59a8a329c", "1e453acf52cb", "becc7fa40ce6", "90343a82a7c8", "47742ee9eb7a", "c133d5a7a11e", "72256bdebf34"]


# --- monitoring ------------------------------------------------------------


def test_health_before_the_collector_has_ever_run(db_session):
    health = evidence_health(db_session, now=NOW, policy=POLICY, settings=Settings())
    assert health.healthy is False and "never run" in health.problems[0]
    assert health.latest_run_at is None and all(not c.expected for c in health.checks)


def test_health_right_after_a_run_and_staleness_thresholds(db_session):
    _cycle(db_session, NOW)
    health = evidence_health(db_session, now=NOW + timedelta(minutes=10), policy=POLICY, settings=Settings())
    assert health.healthy and health.latest_successful_run_at == NOW
    assert (health.upcoming_games, health.games_in_lineup_window, health.current_availability_entries) == (2, 1, 1)
    by_kind = {c.kind: c for c in health.checks}
    assert by_kind["availability"].stale_after_minutes == 90 and by_kind["availability"].age_minutes == 10
    assert by_kind["game_lineup"].expected and not by_kind["box_scores"].expected  # no game has finished yet

    stale = evidence_health(db_session, now=NOW + timedelta(minutes=91), policy=POLICY, settings=Settings())
    assert not stale.healthy and [c.kind for c in stale.checks if c.stale] == ["availability"]


def test_lineups_are_not_expected_when_no_game_is_near_tipoff(db_session):
    _cycle(db_session, NOW, stats=FakeStats([_record("far", NOW + timedelta(days=5))]))
    relaxed = replace(POLICY, availability_interval=timedelta(hours=10), schedule_interval=timedelta(hours=10))
    health = evidence_health(db_session, now=NOW + timedelta(hours=5), policy=relaxed, settings=Settings())
    lineup = next(c for c in health.checks if c.kind == "game_lineup")
    assert lineup.expected is False and lineup.stale is False and health.healthy


def test_an_unfinished_latest_run_is_reported(db_session):
    _cycle(db_session, NOW)
    db_session.add(NbaLiveCycleRun(started_at=NOW + timedelta(minutes=1), status="in_progress", steps=[]))
    db_session.commit()
    health = evidence_health(db_session, now=NOW + timedelta(hours=1), policy=replace(POLICY, availability_interval=timedelta(hours=10)), settings=Settings())
    assert any("never finished" in p for p in health.problems)


def test_status_api_carries_live_evidence_health(client, db_session):
    body = client.get("/api/nba/status").json()["live_evidence"]
    assert body["healthy"] is False and body["latest_run_at"] is None
    assert {c["kind"] for c in body["checks"]} == {"availability", "schedule", "team_roster", "team_depth_chart", "game_lineup", "box_scores"}
    for field in ("upcoming_games", "games_in_lineup_window", "current_availability_entries", "latest_successful_run_at", "problems"):
        assert field in body


# --- the workflow file itself ----------------------------------------------


def test_workflow_is_gated_serialised_and_uses_the_existing_database_secret():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'cron: "11,26,41,56 * * * *"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "group: nba-live-cycle" in workflow and "cancel-in-progress: false" in workflow
    assert "vars.NBA_LIVE_CYCLE_ENABLED == 'true'" in workflow
    assert "secrets.DATABASE_URL" in workflow and "DATABASE_URL=" not in workflow.replace("-e DATABASE_URL", "")
    assert "vars.NBA_LIVE_CYCLE_IMAGE" in workflow and "ghcr.io/srat0029-ui" not in workflow  # the image comes only from the pinned variable
    assert "alembic upgrade" not in workflow  # the job never migrates
    assert "THE_ODDS_API_KEY" not in workflow  # no odds quota can be spent


def test_cloudflare_dispatch_is_gated_like_the_schedule():
    """The Cloudflare cron dispatches with trigger=cloudflare-cron; an
    automated dispatch must honour NBA_LIVE_CYCLE_ENABLED, while a human
    dispatch (trigger left at its default) still always runs."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'cron: "11,26,41,56 * * * *"' in workflow  # GitHub schedule kept as the backup trigger
    assert "trigger:" in workflow and "default: manual" in workflow
    assert "dispatch_id:" in workflow
    assert (
        "(github.event_name == 'workflow_dispatch' && inputs.trigger != 'cloudflare-cron')\n"
        "      || vars.NBA_LIVE_CYCLE_ENABLED == 'true'"
    ) in workflow
    assert "format('NBA Live Cycle [{0}]', inputs.dispatch_id)" in workflow
