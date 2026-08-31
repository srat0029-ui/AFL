from datetime import datetime, timedelta, timezone

from app.models import LiveCycleRun, PropMarketObservation
from app.models.live_cycle_run import RUN_BLOCKED, RUN_OK, RUN_PARTIAL, STEP_BLOCKING_FAILURE, STEP_RECOVERABLE_FAILURE, STEP_SUCCESS
from app.trading_monitor.data_health import SEVERITY_INFO, SEVERITY_WARNING, load_data_health

NOW = datetime.now(timezone.utc)


class TestLoadDataHealth:
    def test_empty_db_does_not_crash_and_reports_not_available(self, db_session):
        report = load_data_health(db_session)
        assert report.backlog.prop_observations_unsettled == 0
        assert report.live_cycle.n_runs_checked == 0
        assert all(f.severity == SEVERITY_INFO for f in report.findings if f.category != "live_cycle")

    def test_settlement_backlog_counts_unsettled_rows(self, db_session):
        db_session.add(PropMarketObservation(
            quote_id=1, match_id=1, player_id=1, bookmaker_id=1, market_type="player_disposals", line_type="over_under",
            threshold=20.5, source="the_odds_api", offered_odds=1.9, observed_at=NOW, raw_implied_probability=0.53,
            model_probability=0.5, model_fair_odds=2.0, predicted_mean=20.0, model_name="disposal_nb", model_version="v1",
            data_cutoff=NOW, confidence_tier="higher_confidence", selection_status_at_observation="confirmed_selected",
            difference_pp=-0.03, expected_value=0.0,
        ))
        db_session.commit()

        report = load_data_health(db_session)

        assert report.backlog.prop_observations_unsettled == 1

    def test_live_cycle_all_ok_reports_no_error_findings(self, db_session):
        db_session.add(LiveCycleRun(run_at=NOW, finished_at=NOW, overall_status=RUN_OK, steps=[{"step": "refresh_fixtures", "status": STEP_SUCCESS, "detail": "ok"}]))
        db_session.commit()

        report = load_data_health(db_session)

        assert report.live_cycle.last_run_status == RUN_OK
        assert report.live_cycle.n_runs_with_failures == 0
        assert not any(f.category == "live_cycle" for f in report.findings)

    def test_recent_failed_step_is_reported(self, db_session):
        db_session.add(LiveCycleRun(
            run_at=NOW, finished_at=NOW, overall_status=RUN_PARTIAL,
            steps=[{"step": "refresh_prop_odds", "status": STEP_RECOVERABLE_FAILURE, "detail": "timed out"}],
        ))
        db_session.commit()

        report = load_data_health(db_session)

        assert report.live_cycle.n_runs_with_failures == 1
        assert "refresh_prop_odds" in report.live_cycle.recent_failed_steps
        live_cycle_findings = [f for f in report.findings if f.category == "live_cycle"]
        assert len(live_cycle_findings) == 1
        assert live_cycle_findings[0].severity == SEVERITY_WARNING

    def test_blocking_failure_is_reported_as_error(self, db_session):
        db_session.add(LiveCycleRun(
            run_at=NOW, finished_at=NOW, overall_status=RUN_BLOCKED,
            steps=[{"step": "identify_upcoming_round", "status": STEP_BLOCKING_FAILURE, "detail": "no fixtures"}],
        ))
        db_session.commit()

        report = load_data_health(db_session)

        live_cycle_findings = [f for f in report.findings if f.category == "live_cycle"]
        assert len(live_cycle_findings) == 1
        assert live_cycle_findings[0].severity == "error"

    def test_only_checks_the_configured_number_of_recent_runs(self, db_session):
        for i in range(10):
            db_session.add(LiveCycleRun(run_at=NOW - timedelta(hours=i), finished_at=NOW, overall_status=RUN_OK, steps=[]))
        db_session.commit()

        report = load_data_health(db_session)

        assert report.live_cycle.n_runs_checked == 5  # STALE_LIVE_CYCLE_RUNS_TO_CHECK
