from datetime import datetime, timedelta, timezone

from app.models import Match, MatchStatus, Round, Season, SgmPriceSnapshot, Sport, Team
from app.player_modelling.sgm_prospective_evaluation import (
    MIN_SAMPLE_FOR_LABELED,
    _closing_snapshot_per_combo,
    _correlation_adjustment_bucket,
    _split,
    load_sgm_prospective_evaluation,
)

NOW = datetime.now(timezone.utc)


def _snap(
    *, match_id=1, leg_signature="h2h:1:None|disposals:2:21.5", n_legs=2, leg_type_combination="disposals+h2h",
    horizon="24h_plus", hours_to_kickoff=30.0, model_probability=0.3, naive_independence_probability=0.28,
    correlation_adjustment_pp=2.0, outcome=None, bookmaker_implied_probability=None,
) -> SgmPriceSnapshot:
    return SgmPriceSnapshot(
        match_id=match_id, leg_signature=leg_signature, n_legs=n_legs, leg_type_combination=leg_type_combination,
        snapshot_horizon=horizon, hours_to_kickoff=hours_to_kickoff, model_name="sgm_joint_conditional_mc",
        model_version="v1", generated_at=NOW, model_probability=model_probability,
        naive_independence_probability=naive_independence_probability, correlation_adjustment_pp=correlation_adjustment_pp,
        model_fair_odds=1 / model_probability, naive_independence_fair_odds=1 / naive_independence_probability,
        mc_standard_error=0.003, n_simulations=20000, dependence_validated=True, outcome=outcome,
        bookmaker_implied_probability=bookmaker_implied_probability,
    )


def _seed_match(db):
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
        scheduled_start=NOW + timedelta(days=1), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match


class TestCorrelationAdjustmentBucket:
    def test_buckets(self):
        assert "Negligible" in _correlation_adjustment_bucket(_snap(correlation_adjustment_pp=0.1))
        assert "Moderate" in _correlation_adjustment_bucket(_snap(correlation_adjustment_pp=1.0))
        assert "Large" in _correlation_adjustment_bucket(_snap(correlation_adjustment_pp=5.0))
        assert "Negligible" in _correlation_adjustment_bucket(_snap(correlation_adjustment_pp=-0.1))  # magnitude, not signed


class TestClosingSnapshotPerCombo:
    def test_picks_lowest_hours_to_kickoff_per_combo(self):
        early = _snap(leg_signature="a", hours_to_kickoff=20.0, outcome="won")
        late = _snap(leg_signature="a", hours_to_kickoff=2.0, outcome="won")
        other_combo = _snap(leg_signature="b", hours_to_kickoff=10.0, outcome="lost")

        result = _closing_snapshot_per_combo([early, late, other_combo])

        assert len(result) == 2
        assert late in result and early not in result
        assert other_combo in result

    def test_different_matches_are_different_combos_even_with_same_signature(self):
        m1 = _snap(match_id=1, leg_signature="a", outcome="won")
        m2 = _snap(match_id=2, leg_signature="a", outcome="lost")

        result = _closing_snapshot_per_combo([m1, m2])

        assert len(result) == 2


class TestSplit:
    def test_below_minimum_is_exploratory(self):
        snaps = [_snap(outcome="won") for _ in range(MIN_SAMPLE_FOR_LABELED - 1)]
        split = _split(snaps, "test")
        assert split.exploratory is True

    def test_at_minimum_is_not_exploratory(self):
        snaps = [_snap(outcome="won" if i % 2 == 0 else "lost", model_probability=0.6 if i % 2 == 0 else 0.4) for i in range(MIN_SAMPLE_FOR_LABELED)]
        split = _split(snaps, "test")
        assert split.exploratory is False

    def test_push_and_void_excluded_from_scoring(self):
        scoreable = [_snap(outcome="won") for _ in range(5)]
        unscoreable = [_snap(outcome="push"), _snap(outcome="void"), _snap(outcome=None)]
        split = _split(scoreable + unscoreable, "test")
        assert split.n_settled == 8  # counts every row passed in
        # only the 5 won/lost rows feed Brier/log-loss
        assert split.model_brier == _expected_brier(scoreable)

    def test_no_bookmaker_data_reports_none_not_error(self):
        snaps = [_snap(outcome="won") for _ in range(5)]
        split = _split(snaps, "test")
        assert split.bookmaker_brier is None
        assert split.bookmaker_log_loss is None
        assert split.n_with_bookmaker_price == 0

    def test_bookmaker_data_scored_when_present(self):
        snaps = [_snap(outcome="won", bookmaker_implied_probability=0.5) for _ in range(5)]
        split = _split(snaps, "test")
        assert split.bookmaker_brier is not None
        assert split.n_with_bookmaker_price == 5

    def test_empty_split_reports_none_not_crash(self):
        split = _split([], "test")
        assert split.model_brier is None
        assert split.exploratory is True
        assert split.n_settled == 0


def _expected_brier(snaps: list[SgmPriceSnapshot]) -> float:
    from app.modelling.metrics import brier_score
    return brier_score([s.model_probability for s in snaps], [1.0 for _ in snaps])


class TestLoadSgmProspectiveEvaluation:
    def test_no_snapshots_reports_accumulating(self, db_session):
        report = load_sgm_prospective_evaluation(db_session)
        assert report.has_settled_data is False
        assert "Accumulating" in report.message

    def test_unsettled_snapshots_still_report_accumulating(self, db_session):
        match = _seed_match(db_session)
        db_session.add(_snap(match_id=match.id, outcome=None))
        db_session.commit()

        report = load_sgm_prospective_evaluation(db_session)

        assert report.has_settled_data is False
        assert report.n_frozen_total == 1

    def test_settled_snapshots_populate_overall_and_splits(self, db_session):
        match = _seed_match(db_session)
        for i in range(MIN_SAMPLE_FOR_LABELED):
            outcome = "won" if i % 2 == 0 else "lost"
            prob = 0.6 if outcome == "won" else 0.4
            db_session.add(_snap(
                match_id=match.id, leg_signature=f"combo-{i}", outcome=outcome, model_probability=prob,
                n_legs=2, leg_type_combination="disposals+h2h", horizon="24h_plus",
            ))
        db_session.commit()

        report = load_sgm_prospective_evaluation(db_session)

        assert report.has_settled_data is True
        assert report.n_settled == MIN_SAMPLE_FOR_LABELED
        assert report.overall is not None
        assert report.overall.exploratory is False
        assert len(report.by_n_legs) == 1
        assert report.by_n_legs[0].label == "2"
        assert len(report.by_leg_combination) == 1
        assert len(report.by_snapshot_horizon) == 1
        assert report.by_snapshot_horizon[0].label == "24h_plus"

    def test_overall_dedupes_across_horizons_but_by_horizon_does_not(self, db_session):
        match = _seed_match(db_session)
        # The SAME real combo, frozen at two different horizons - both settled won.
        db_session.add(_snap(match_id=match.id, leg_signature="same-combo", outcome="won", horizon="24h_plus", hours_to_kickoff=30.0))
        db_session.add(_snap(match_id=match.id, leg_signature="same-combo", outcome="won", horizon="1h_6h", hours_to_kickoff=3.0))
        db_session.commit()

        report = load_sgm_prospective_evaluation(db_session)

        assert report.n_unique_combos == 1  # deduped to the closing snapshot
        assert report.overall.n_settled == 1
        total_across_horizon_splits = sum(s.n_settled for s in report.by_snapshot_horizon)
        assert total_across_horizon_splits == 2  # by-horizon deliberately does NOT dedupe
