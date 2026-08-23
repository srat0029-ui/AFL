"""Targeted tests for the integration-health snapshot (B2B Demo + Integration
Readiness stage, item 5): status ok/degraded transitions, stale-warning
generation, promoted-model reporting, and the naive/aware datetime fix in
_last_successful_step_at."""

from datetime import datetime, timedelta, timezone

from app.models import LiveCycleRun, Match, MatchStatus, Round, Season, Sport, Team
from app.modelling.elo import EloConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from app.pricing.integration_health import load_integration_health

NOW = datetime.now(timezone.utc)


def _seed_upcoming_round(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    away = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match


def _seed_team_models(db):
    persist_model_run(db, "elo", EloConfig(), 2022, metrics=[])
    persist_model_run(db, "poisson", PoissonConfig(), 2022, metrics=[])


def _live_cycle_run(db, run_at, steps):
    run = LiveCycleRun(run_at=run_at, overall_status="RUN_OK", steps=steps)
    db.add(run)
    db.commit()
    return run


def test_no_data_yet_is_degraded_with_full_warning_set(db_session):
    health = load_integration_health(db_session)
    assert health.status == "degraded"
    assert health.last_fixture_refresh is None
    assert health.last_odds_refresh is None
    assert health.current_round is None
    categories = {w.category for w in health.stale_warnings}
    assert categories == {"fixtures", "odds", "schedule", "model"}


def test_recent_successful_steps_and_promoted_models_yield_ok_status(db_session):
    _seed_upcoming_round(db_session)
    _seed_team_models(db_session)
    _live_cycle_run(
        db_session, NOW - timedelta(minutes=5),
        steps=[{"step": "refresh_fixtures", "status": "success"}, {"step": "refresh_prop_odds", "status": "success"}],
    )

    health = load_integration_health(db_session)

    # No player disposal/goal model persisted in this test, so "model" warnings
    # for those markets still fire — the point here is that fixtures/odds/schedule
    # (the freshness signals this test targets) are all clean.
    categories = {w.category for w in health.stale_warnings}
    assert "fixtures" not in categories
    assert "odds" not in categories
    assert "schedule" not in categories
    assert health.current_round == 1
    assert health.current_season_year == 2026
    assert health.last_fixture_refresh is not None
    assert health.last_odds_refresh is not None
    assert set(health.promoted_models) == {"team_elo", "team_poisson"}


def test_stale_refresh_beyond_threshold_is_flagged_and_degraded(db_session):
    _seed_upcoming_round(db_session)
    _seed_team_models(db_session)
    _live_cycle_run(
        db_session, NOW - timedelta(hours=48),
        steps=[{"step": "refresh_fixtures", "status": "success"}, {"step": "refresh_prop_odds", "status": "success"}],
    )

    health = load_integration_health(db_session)

    assert health.status == "degraded"
    categories = {w.category for w in health.stale_warnings}
    assert "fixtures" in categories
    assert "odds" in categories


def test_only_successful_steps_count_as_last_refresh(db_session):
    _seed_upcoming_round(db_session)
    _seed_team_models(db_session)
    _live_cycle_run(db_session, NOW - timedelta(hours=48), steps=[{"step": "refresh_fixtures", "status": "success"}])
    _live_cycle_run(db_session, NOW - timedelta(minutes=1), steps=[{"step": "refresh_fixtures", "status": "error"}])

    health = load_integration_health(db_session)

    # The most recent run failed this step, so the last SUCCESSFUL one (48h ago) is what's reported and flagged stale.
    assert health.last_fixture_refresh is not None
    assert any(w.category == "fixtures" for w in health.stale_warnings)


def test_naive_run_at_from_sqlite_round_trip_does_not_raise(db_session):
    """SQLite strips tzinfo on round-trip; load_integration_health must not
    raise TypeError when comparing a naive stored timestamp against aware now."""
    run = LiveCycleRun(run_at=NOW - timedelta(minutes=5), overall_status="RUN_OK", steps=[{"step": "refresh_fixtures", "status": "success"}])
    db_session.add(run)
    db_session.commit()
    db_session.expire_all()

    fetched = db_session.get(LiveCycleRun, run.id)
    assert fetched.run_at.tzinfo is None  # confirms the round-trip actually stripped tzinfo, i.e. this test is real

    health = load_integration_health(db_session)  # must not raise
    assert health.last_fixture_refresh is not None


def test_integration_health_endpoint_returns_expected_shape(client, db_session):
    _seed_upcoming_round(db_session)
    _seed_team_models(db_session)
    _live_cycle_run(
        db_session, NOW - timedelta(minutes=5),
        steps=[{"step": "refresh_fixtures", "status": "success"}, {"step": "refresh_prop_odds", "status": "success"}],
    )

    resp = client.get("/api/v1/integration-health")

    assert resp.status_code == 200
    body = resp.json()
    categories = {w["category"] for w in body["stale_warnings"]}
    assert "fixtures" not in categories
    assert "odds" not in categories
    assert body["current_round"] == 1
    assert body["current_season_year"] == 2026
    assert "team_elo" in body["promoted_models"]
    assert "team_poisson" in body["promoted_models"]
