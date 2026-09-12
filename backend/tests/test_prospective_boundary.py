"""Tests for the formal PRODUCTION_PROSPECTIVE_TRACKING_START boundary
(app/prospective_boundary.py) and every consumer that respects it:
real_market_tracking.py (PropMarketObservation + the PlayerPropMarket raw
quote count), prospective_evaluation.py (PricingSnapshot),
sgm_prospective_evaluation.py (SgmPriceSnapshot), and
market_monitor/effectiveness.py + prospective_coverage.py
(AnomalyCaseSnapshot/AnomalyCaseFollowUp).

Every test here proves the filter is READ-TIME ONLY - a pre-boundary row
always remains physically present in the database, never deleted,
mutated, or reclassified; it is simply excluded from a boundary-scoped
aggregation. Current-operational-state metrics (active_match_ids-derived
counts, currently-open case counts) are proven to stay unfiltered.
"""

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.market_monitor.effectiveness import compute_effectiveness_summary
from app.market_monitor.prospective_coverage import compute_prospective_coverage
from app.models import (
    AnomalyCaseFollowUp,
    AnomalyCaseSnapshot,
    Bookmaker,
    Match,
    MatchStatus,
    Player,
    PlayerPropMarket,
    PricingSnapshot,
    PropMarketObservation,
    Round,
    Season,
    SgmPriceSnapshot,
    Sport,
    Team,
)
from app.player_modelling.prospective_evaluation import load_prospective_evaluation
from app.player_modelling.real_market_tracking import coverage_metrics, load_real_market_tracking_report
from app.player_modelling.sgm_prospective_evaluation import load_sgm_prospective_evaluation
from app.prospective_boundary import PRODUCTION_PROSPECTIVE_TRACKING_START, prospective_tracking_start

BOUNDARY = datetime(2026, 9, 10, 8, 32, 51, 944004, tzinfo=timezone.utc)
JUST_BEFORE = BOUNDARY - timedelta(microseconds=1)
JUST_AFTER = BOUNDARY + timedelta(microseconds=1)


def _aware(dt: datetime) -> datetime:
    """SQLite's driver drops tzinfo on round-trip (DateTime(timezone=True)
    is a schema-shape-only guarantee there, not enforced at the driver
    level - see app/models/pricing_snapshot.py's own docstring on this
    exact SQLite limitation) - values read back in these tests are always
    known-UTC, so this just restores the tzinfo for comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# --- shared seed helpers -----------------------------------------------------


def _seed_match(db, *, scheduled_start=None, status=MatchStatus.SCHEDULED):
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
        scheduled_start=scheduled_start or (BOUNDARY + timedelta(days=1)), status=status,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db.add_all([player, bookmaker])
    db.commit()
    return match, player, bookmaker


def _pricing_snapshot(match, player, *, generated_at, outcome=None, threshold=20.5):
    return PricingSnapshot(
        match_id=match.id, player_id=player.id, market_family="player_disposals", market_type="player_disposals",
        selection="over", line_type="over_under", threshold=threshold, line_value=None,
        model_name="disposal_nb", model_version="v1", generated_at=generated_at, data_cutoff=generated_at,
        lineup_status="expected_in", confidence_tier="higher_confidence",
        model_probability=0.6, model_fair_odds=1.67, outcome=outcome, settled_at=generated_at if outcome else None,
    )


def _sgm_snapshot(match, *, generated_at, leg_signature="a", outcome=None):
    return SgmPriceSnapshot(
        match_id=match.id, leg_signature=leg_signature, n_legs=2, leg_type_combination="disposals+h2h",
        snapshot_horizon="24h_plus", hours_to_kickoff=30.0, model_name="sgm_joint_conditional_mc", model_version="v1",
        generated_at=generated_at, model_probability=0.3, naive_independence_probability=0.28,
        correlation_adjustment_pp=2.0, model_fair_odds=1 / 0.3, naive_independence_fair_odds=1 / 0.28,
        mc_standard_error=0.003, n_simulations=20000, dependence_validated=True, outcome=outcome,
    )


def _prop_observation(db, match, player, bookmaker, *, observed_at, threshold=29.5):
    # A real, flushed PlayerPropMarket row - quote_id is a genuine foreign
    # key (enforced on Postgres, silently unenforced on SQLite by default),
    # so a placeholder id here would only work by accident on SQLite.
    quote = _raw_quote(match, player, bookmaker, recorded_at=observed_at, threshold=threshold)
    db.add(quote)
    db.flush()
    return PropMarketObservation(
        quote_id=quote.id, match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id,
        market_type="player_disposals", line_type="over_under", threshold=threshold, source="the_odds_api",
        offered_odds=1.9, observed_at=observed_at, raw_implied_probability=0.526, devigged_probability=None,
        overround_removed=False, model_probability=0.55, model_fair_odds=1.82, predicted_mean=28.0,
        model_name="disposals_nb", model_version="v1", data_cutoff=observed_at,
        confidence_tier="moderate_confidence", selection_status_at_observation="placeholder",
        is_confirmed_at_observation=False, difference_pp=0.02, expected_value=0.045,
    )


def _raw_quote(match, player, bookmaker, *, recorded_at, threshold=29.5, source="the_odds_api"):
    return PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=threshold, selection="over", price_decimal=1.9,
        recorded_at=recorded_at, source=source,
    )


def _case_snapshot(match, *, case_id, frozen_at, capture_mode="prospective", resolved_at=None):
    return AnomalyCaseSnapshot(
        case_id=case_id, match_id=match.id, market_type="player_disposals", capture_mode=capture_mode,
        alert_types=[], priority_score=50.0, priority_components=[], bookmaker_prices_at_freeze=[],
        n_bookmakers_at_freeze=2, first_seen_at=frozen_at, persistence_n_snapshots_at_freeze=1,
        frozen_at=frozen_at, model_risk_flags=[], resolved_at=resolved_at,
        outcome_codes=["persisted_to_kickoff"] if resolved_at else None,
    )


def _followup(snapshot, *, stage_bucket, captured_at, hours_to_kickoff=10.0):
    return AnomalyCaseFollowUp(
        snapshot_id=snapshot.id, case_id=snapshot.case_id, stage_bucket=stage_bucket,
        hours_to_kickoff=hours_to_kickoff, captured_at=captured_at,
    )


# --- 1. The accessor itself --------------------------------------------------


class TestProspectiveTrackingStartAccessor:
    def test_production_returns_the_exact_fixed_boundary(self):
        settings = Settings(app_env="production")
        result = prospective_tracking_start(settings)
        assert result == PRODUCTION_PROSPECTIVE_TRACKING_START
        assert result == datetime(2026, 9, 10, 8, 32, 51, 944004, tzinfo=timezone.utc)

    def test_local_returns_none(self):
        settings = Settings(app_env="local")
        assert prospective_tracking_start(settings) is None

    def test_test_env_returns_none(self):
        settings = Settings(app_env="test")
        assert prospective_tracking_start(settings) is None

    def test_missing_app_env_cannot_silently_select_production(self):
        """There is no code path where an unset/blank app_env resolves to
        the production boundary - Settings.app_env defaults to "local",
        and prospective_tracking_start only returns the real boundary on
        an EXACT "production" match, never a fallback/default branch."""
        settings = Settings()
        assert settings.app_env == "local"
        assert prospective_tracking_start(settings) is None


# --- 2. Boundary edge precision (PricingSnapshot as the representative table) -


class TestBoundaryEdgePrecision:
    def test_one_microsecond_before_boundary_excluded(self, db_session):
        match, player, _bm = _seed_match(db_session)
        db_session.add(_pricing_snapshot(match, player, generated_at=JUST_BEFORE, outcome="won"))
        db_session.commit()

        report = load_prospective_evaluation(db_session, boundary=BOUNDARY)
        assert report.has_settled_data is False
        assert report.n_frozen_total == 0

    def test_exactly_at_boundary_included(self, db_session):
        match, player, _bm = _seed_match(db_session)
        db_session.add(_pricing_snapshot(match, player, generated_at=BOUNDARY, outcome="won"))
        db_session.commit()

        report = load_prospective_evaluation(db_session, boundary=BOUNDARY)
        assert report.n_frozen_total == 1
        assert report.has_settled_data is True

    def test_after_boundary_included(self, db_session):
        match, player, _bm = _seed_match(db_session)
        db_session.add(_pricing_snapshot(match, player, generated_at=JUST_AFTER, outcome="won"))
        db_session.commit()

        report = load_prospective_evaluation(db_session, boundary=BOUNDARY)
        assert report.n_frozen_total == 1

    def test_pre_boundary_rows_remain_physically_present_and_unchanged(self, db_session):
        from sqlalchemy import select

        match, player, _bm = _seed_match(db_session)
        pre = _pricing_snapshot(match, player, generated_at=JUST_BEFORE, outcome="won")
        db_session.add(pre)
        db_session.commit()
        pre_id = pre.id

        report = load_prospective_evaluation(db_session, boundary=BOUNDARY)
        assert report.n_frozen_total == 0  # excluded from the boundary-scoped report...

        # ...but a plain, unfiltered query proves the row is still there, untouched.
        still_there = db_session.scalar(select(PricingSnapshot).where(PricingSnapshot.id == pre_id))
        assert still_there is not None
        assert _aware(still_there.generated_at) == JUST_BEFORE
        assert still_there.outcome == "won"


# --- 3. Real Market Tracking --------------------------------------------------


class TestRealMarketTrackingBoundary:
    def test_report_excludes_pre_boundary_observations(self, db_session):
        match, player, bookmaker = _seed_match(db_session)
        db_session.add(_prop_observation(db_session, match, player, bookmaker, observed_at=JUST_BEFORE))
        db_session.add(_prop_observation(db_session, match, player, bookmaker, observed_at=JUST_AFTER, threshold=31.5))
        db_session.commit()

        report = load_real_market_tracking_report(db_session, boundary=BOUNDARY)
        assert report.summary.total_observations == 1
        assert _aware(report.summary.earliest_observed_at) == JUST_AFTER

    def test_report_without_boundary_reports_full_history(self, db_session):
        """The default (None) preserves existing local/dev/test behavior."""
        match, player, bookmaker = _seed_match(db_session)
        db_session.add(_prop_observation(db_session, match, player, bookmaker, observed_at=JUST_BEFORE))
        db_session.add(_prop_observation(db_session, match, player, bookmaker, observed_at=JUST_AFTER, threshold=31.5))
        db_session.commit()

        report = load_real_market_tracking_report(db_session)
        assert report.summary.total_observations == 2

    def test_raw_automated_quote_count_excludes_pre_boundary_quotes(self, db_session):
        match, player, bookmaker = _seed_match(db_session)
        db_session.add(_raw_quote(match, player, bookmaker, recorded_at=JUST_BEFORE))
        db_session.add(_raw_quote(match, player, bookmaker, recorded_at=JUST_AFTER, threshold=31.5))
        db_session.commit()

        metrics = coverage_metrics(db_session, [], boundary=BOUNDARY)
        assert metrics.total_raw_quotes == 1

    def test_raw_quote_count_without_boundary_counts_all(self, db_session):
        match, player, bookmaker = _seed_match(db_session)
        db_session.add(_raw_quote(match, player, bookmaker, recorded_at=JUST_BEFORE))
        db_session.add(_raw_quote(match, player, bookmaker, recorded_at=JUST_AFTER, threshold=31.5))
        db_session.commit()

        metrics = coverage_metrics(db_session, [])
        assert metrics.total_raw_quotes == 2


# --- 4. PricingSnapshot prospective evaluation --------------------------------


class TestPricingSnapshotEvaluationBoundary:
    def test_excludes_pre_boundary_rows(self, db_session):
        match, player, _bm = _seed_match(db_session)
        db_session.add(_pricing_snapshot(match, player, generated_at=JUST_BEFORE, outcome="won", threshold=20.5))
        db_session.add(_pricing_snapshot(match, player, generated_at=JUST_AFTER, outcome="lost", threshold=25.5))
        db_session.commit()

        report = load_prospective_evaluation(db_session, boundary=BOUNDARY)
        assert report.n_frozen_total == 1
        assert report.n_settled == 1


# --- 5. SGM prospective evaluation --------------------------------------------


class TestSgmEvaluationBoundary:
    def test_excludes_pre_boundary_rows(self, db_session):
        match, _player, _bm = _seed_match(db_session)
        db_session.add(_sgm_snapshot(match, generated_at=JUST_BEFORE, leg_signature="pre", outcome="won"))
        db_session.add(_sgm_snapshot(match, generated_at=JUST_AFTER, leg_signature="post", outcome="lost"))
        db_session.commit()

        report = load_sgm_prospective_evaluation(db_session, boundary=BOUNDARY)
        assert report.n_frozen_total == 1

    def test_without_boundary_reports_full_history(self, db_session):
        match, _player, _bm = _seed_match(db_session)
        db_session.add(_sgm_snapshot(match, generated_at=JUST_BEFORE, leg_signature="pre", outcome="won"))
        db_session.add(_sgm_snapshot(match, generated_at=JUST_AFTER, leg_signature="post", outcome="lost"))
        db_session.commit()

        report = load_sgm_prospective_evaluation(db_session)
        assert report.n_frozen_total == 2


# --- 6. Market Monitor effectiveness -----------------------------------------


class TestEffectivenessBoundary:
    def test_prospective_mode_excludes_pre_boundary_cases(self, db_session):
        match, _player, _bm = _seed_match(db_session)
        db_session.add(_case_snapshot(match, case_id="c-pre", frozen_at=JUST_BEFORE))
        db_session.add(_case_snapshot(match, case_id="c-post", frozen_at=JUST_AFTER))
        db_session.commit()

        summary = compute_effectiveness_summary(db_session, capture_mode="prospective", boundary=BOUNDARY)
        assert summary.n_frozen_cases == 1

    def test_retrospective_mode_ignores_the_boundary_entirely(self, db_session):
        """A deliberately retrospective report must keep its own intended
        historical behavior - the boundary must never be silently applied
        to it, even if a caller passes one in."""
        match, _player, _bm = _seed_match(db_session)
        db_session.add(_case_snapshot(match, case_id="c-pre", frozen_at=JUST_BEFORE, capture_mode="retrospective"))
        db_session.commit()

        summary = compute_effectiveness_summary(db_session, capture_mode="retrospective", boundary=BOUNDARY)
        assert summary.n_frozen_cases == 1  # NOT excluded, despite being before the boundary

    def test_without_boundary_reports_full_prospective_history(self, db_session):
        match, _player, _bm = _seed_match(db_session)
        db_session.add(_case_snapshot(match, case_id="c-pre", frozen_at=JUST_BEFORE))
        db_session.commit()

        summary = compute_effectiveness_summary(db_session, capture_mode="prospective")
        assert summary.n_frozen_cases == 1


# --- 7. Prospective Coverage: operational-state vs accumulated evidence ------


class TestProspectiveCoverageBoundary:
    def test_upcoming_matches_monitored_ignores_the_boundary(self, db_session):
        """Current operational state - proven unaffected regardless of
        whether a boundary is supplied."""
        _seed_match(db_session, scheduled_start=datetime.now(timezone.utc) + timedelta(days=1), status=MatchStatus.SCHEDULED)

        without_boundary = compute_prospective_coverage(db_session)
        with_boundary = compute_prospective_coverage(db_session, boundary=BOUNDARY)
        assert without_boundary.n_upcoming_matches_monitored == with_boundary.n_upcoming_matches_monitored

    def test_currently_open_case_count_ignores_the_boundary(self, db_session):
        match, _player, _bm = _seed_match(db_session)
        db_session.add(_case_snapshot(match, case_id="open-pre-boundary", frozen_at=JUST_BEFORE, resolved_at=None))
        db_session.commit()

        without_boundary = compute_prospective_coverage(db_session)
        with_boundary = compute_prospective_coverage(db_session, boundary=BOUNDARY)
        assert without_boundary.n_frozen_cases == 1
        assert with_boundary.n_frozen_cases == 1  # NOT filtered - still open, still current

    def test_followup_counts_exclude_cases_frozen_before_the_boundary(self, db_session):
        match, _player, _bm = _seed_match(db_session)
        pre_case = _case_snapshot(match, case_id="pre", frozen_at=JUST_BEFORE)
        post_case = _case_snapshot(match, case_id="post", frozen_at=JUST_AFTER)
        db_session.add_all([pre_case, post_case])
        db_session.flush()
        # Both cases get 2 follow-ups each - only the post-boundary case's
        # should count once the boundary is applied.
        for case in (pre_case, post_case):
            db_session.add(_followup(case, stage_bucket="24h_plus", captured_at=JUST_AFTER))
            db_session.add(_followup(case, stage_bucket="6_24h", captured_at=JUST_AFTER))
        db_session.commit()

        without_boundary = compute_prospective_coverage(db_session)
        with_boundary = compute_prospective_coverage(db_session, boundary=BOUNDARY)
        assert without_boundary.n_cases_with_2plus_followups == 2
        assert with_boundary.n_cases_with_2plus_followups == 1

    def test_post_boundary_followup_on_pre_boundary_parent_cannot_leak_in(self, db_session):
        """The exact leak scenario: a follow-up CAPTURED after the boundary,
        but whose PARENT case was frozen BEFORE it. Eligibility must be
        decided by the parent's frozen_at, never the follow-up's own
        captured_at."""
        match, _player, _bm = _seed_match(db_session)
        pre_case = _case_snapshot(match, case_id="pre-parent", frozen_at=JUST_BEFORE)
        db_session.add(pre_case)
        db_session.flush()
        # Give it 3 follow-ups, all captured well AFTER the boundary.
        far_after = BOUNDARY + timedelta(days=1)
        db_session.add(_followup(pre_case, stage_bucket="24h_plus", captured_at=far_after))
        db_session.add(_followup(pre_case, stage_bucket="6_24h", captured_at=far_after))
        db_session.add(_followup(pre_case, stage_bucket="1_6h", captured_at=far_after))
        db_session.commit()

        with_boundary = compute_prospective_coverage(db_session, boundary=BOUNDARY)
        assert with_boundary.n_cases_with_2plus_followups == 0
        assert with_boundary.n_cases_with_3plus_followups == 0
        assert with_boundary.earliest_hours_before_kickoff_captured is None

    def test_timing_extremes_respect_the_boundary_via_parent(self, db_session):
        match, _player, _bm = _seed_match(db_session)
        pre_case = _case_snapshot(match, case_id="pre", frozen_at=JUST_BEFORE)
        post_case = _case_snapshot(match, case_id="post", frozen_at=JUST_AFTER)
        db_session.add_all([pre_case, post_case])
        db_session.flush()
        db_session.add(_followup(pre_case, stage_bucket="24h_plus", captured_at=JUST_AFTER, hours_to_kickoff=999.0))
        db_session.add(_followup(post_case, stage_bucket="24h_plus", captured_at=JUST_AFTER, hours_to_kickoff=48.0))
        db_session.commit()

        with_boundary = compute_prospective_coverage(db_session, boundary=BOUNDARY)
        # The 999.0-hour outlier belongs to the pre-boundary case and must
        # not appear once the boundary is applied.
        assert with_boundary.earliest_hours_before_kickoff_captured == 48.0
