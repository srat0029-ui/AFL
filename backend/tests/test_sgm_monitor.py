from datetime import datetime, timezone

import pytest

from app.models import SgmDependenceCoefficient, SgmPriceSnapshot
from app.trading_monitor.sgm_monitor import _bucket_for, _horizon_movements, load_sgm_monitoring

NOW = datetime.now(timezone.utc)


def _snap(
    *, match_id=1, leg_signature="h2h:1:None|disposals:2:21.5", n_legs=2, leg_type_combination="disposals+h2h",
    horizon="24h_plus", hours_to_kickoff=30.0, model_probability=0.3, naive_independence_probability=0.28,
    correlation_adjustment_pp=2.0, mc_standard_error=0.003,
) -> SgmPriceSnapshot:
    return SgmPriceSnapshot(
        match_id=match_id, leg_signature=leg_signature, n_legs=n_legs, leg_type_combination=leg_type_combination,
        snapshot_horizon=horizon, hours_to_kickoff=hours_to_kickoff, model_name="sgm_joint_conditional_mc",
        model_version="v1", generated_at=NOW, model_probability=model_probability,
        naive_independence_probability=naive_independence_probability, correlation_adjustment_pp=correlation_adjustment_pp,
        model_fair_odds=1 / model_probability, naive_independence_fair_odds=1 / naive_independence_probability,
        mc_standard_error=mc_standard_error, n_simulations=20000, dependence_validated=True,
    )


class TestBucketFor:
    def test_negligible(self):
        assert "Negligible" in _bucket_for(0.1)

    def test_moderate(self):
        assert "Moderate" in _bucket_for(1.0)

    def test_large(self):
        assert "Large" in _bucket_for(5.0)


class TestHorizonMovements:
    def test_single_snapshot_produces_no_movement(self):
        assert _horizon_movements([_snap()]) == []

    def test_two_horizons_produce_one_movement(self):
        early = _snap(leg_signature="a", horizon="24h_plus", hours_to_kickoff=30.0, model_probability=0.30)
        late = _snap(leg_signature="a", horizon="under_1h", hours_to_kickoff=0.5, model_probability=0.40)

        movements = _horizon_movements([early, late])

        assert len(movements) == 1
        assert movements[0].earliest_horizon == "24h_plus"
        assert movements[0].latest_horizon == "under_1h"
        assert movements[0].absolute_change == pytest.approx(0.10)

    def test_movement_within_mc_noise_is_flagged_as_such(self):
        early = _snap(leg_signature="a", hours_to_kickoff=30.0, model_probability=0.300, mc_standard_error=0.05)
        late = _snap(leg_signature="a", hours_to_kickoff=0.5, model_probability=0.301, mc_standard_error=0.05)

        movements = _horizon_movements([early, late])

        assert movements[0].is_beyond_mc_noise is False

    def test_movement_beyond_mc_noise_is_flagged_as_such(self):
        early = _snap(leg_signature="a", hours_to_kickoff=30.0, model_probability=0.20, mc_standard_error=0.003)
        late = _snap(leg_signature="a", hours_to_kickoff=0.5, model_probability=0.35, mc_standard_error=0.003)

        movements = _horizon_movements([early, late])

        assert movements[0].is_beyond_mc_noise is True

    def test_different_matches_are_different_combos(self):
        a = _snap(match_id=1, leg_signature="x")
        b = _snap(match_id=2, leg_signature="x")
        assert _horizon_movements([a, b]) == []  # each match+signature only has ONE snapshot here


class TestLoadSgmMonitoring:
    def test_empty_db_returns_empty_report(self, db_session):
        report = load_sgm_monitoring(db_session)
        assert report.n_recent_snapshots == 0
        assert report.largest_naive_vs_joint_differences == []
        assert report.coefficient_provenance == []

    def test_ranks_by_largest_naive_vs_joint_difference(self, db_session):
        db_session.add(_snap(leg_signature="small-diff", model_probability=0.30, naive_independence_probability=0.29))
        db_session.add(_snap(leg_signature="big-diff", model_probability=0.30, naive_independence_probability=0.15))
        db_session.commit()

        report = load_sgm_monitoring(db_session)

        assert report.largest_naive_vs_joint_differences[0].leg_signature == "big-diff"

    def test_ranks_by_largest_correlation_adjustment(self, db_session):
        db_session.add(_snap(leg_signature="small-adj", correlation_adjustment_pp=0.1))
        db_session.add(_snap(leg_signature="big-adj", correlation_adjustment_pp=5.0))
        db_session.commit()

        report = load_sgm_monitoring(db_session)

        assert report.largest_correlation_adjustments[0].leg_signature == "big-adj"
        assert "Large" in report.largest_correlation_adjustments[0].correlation_adjustment_bucket

    def test_reports_coefficient_provenance(self, db_session):
        db_session.add(SgmDependenceCoefficient(
            market="disposals", slope=0.016, intercept=-0.04, n_observations=45216,
            fit_cutoff_year=2023, model_version="sgm_joint_conditional_mc_v1@test", fitted_at=NOW,
        ))
        db_session.commit()

        report = load_sgm_monitoring(db_session)

        assert len(report.coefficient_provenance) == 1
        assert report.coefficient_provenance[0].market == "disposals"
        assert report.coefficient_provenance[0].slope == 0.016
