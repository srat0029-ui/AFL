"""Tests for the Prospective Live Evaluation dashboard: honest
accumulating-data state with zero settled snapshots, correct Brier/log-loss/
model-vs-market computation once settled data exists, push/void exclusion
from scoring, unique-event deduplication, and market-family/probability
bucket/model-version splits — all read from PricingSnapshot only, never
mixed with historical backtest tables."""

from datetime import datetime, timedelta, timezone

from app.models import Match, MatchStatus, Player, PricingSnapshot, Round, Season, Sport, Team
from app.player_modelling.prospective_evaluation import load_prospective_evaluation

NOW = datetime.now(timezone.utc)


def _seed_match_and_player(db):
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
        scheduled_start=NOW - timedelta(days=1), status=MatchStatus.COMPLETED,
    )
    db.add(match)
    player = Player(sport_id=sport.id, display_name="Test Player", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    db.commit()
    return match, player


def _snapshot(match, player=None, *, market_family="player_disposals", market_type="player_disposals", threshold=20.5,
              model_probability=0.5, outcome=None, market_consensus_probability=None, model_version="disposals_huber@1"):
    return PricingSnapshot(
        match_id=match.id, player_id=player.id if player else None, market_family=market_family, market_type=market_type,
        selection="over", line_type="over_under" if player else None, threshold=threshold if player else None,
        line_value=None, model_name="disposal_nb", model_version=model_version, generated_at=NOW - timedelta(days=2),
        data_cutoff=NOW - timedelta(days=2), lineup_status="expected_in", confidence_tier="higher_confidence",
        model_probability=model_probability, model_fair_odds=1 / model_probability,
        market_consensus_probability=market_consensus_probability, outcome=outcome,
        settled_at=NOW if outcome else None,
    )


def test_no_settled_snapshots_is_an_honest_accumulating_state(db_session):
    report = load_prospective_evaluation(db_session)
    assert report.has_settled_data is False
    assert report.n_settled == 0
    assert "Accumulating data" in report.message
    assert report.overall is None


def test_frozen_but_unsettled_snapshots_still_count_toward_total(db_session):
    match, player = _seed_match_and_player(db_session)
    db_session.add(_snapshot(match, player, outcome=None))
    db_session.commit()
    report = load_prospective_evaluation(db_session)
    assert report.has_settled_data is False
    assert report.n_frozen_total == 1
    assert report.n_settled == 0


def test_settled_snapshots_compute_brier_against_known_outcomes(db_session):
    match, player = _seed_match_and_player(db_session)
    # A perfectly-calibrated pair: predicted 1.0 and won, predicted 0.0 and lost -> brier = 0
    db_session.add(_snapshot(match, player, model_probability=0.99, outcome="won", threshold=20.5))
    db_session.add(_snapshot(match, player, model_probability=0.01, outcome="lost", threshold=25.5))
    db_session.commit()

    report = load_prospective_evaluation(db_session)
    assert report.has_settled_data is True
    assert report.n_settled == 2
    assert report.overall.model_brier < 0.001  # near-perfect calibration on this toy pair


def test_push_and_void_count_as_settled_but_are_excluded_from_scoring(db_session):
    match, player = _seed_match_and_player(db_session)
    db_session.add(_snapshot(match, player, model_probability=0.6, outcome="won", threshold=20.5))
    db_session.add(_snapshot(match, player, model_probability=0.6, outcome="push", threshold=25.5))
    db_session.add(_snapshot(match, player, model_probability=0.6, outcome="void", threshold=30.5))
    db_session.commit()

    report = load_prospective_evaluation(db_session)
    assert report.n_settled == 3  # all three count as settled
    assert report.overall.model_brier is not None  # still computed from the one scoreable row


def test_market_consensus_brier_uses_frozen_consensus_probability(db_session):
    match, player = _seed_match_and_player(db_session)
    db_session.add(_snapshot(match, player, model_probability=0.9, outcome="won", market_consensus_probability=0.5, threshold=20.5))
    db_session.commit()

    report = load_prospective_evaluation(db_session)
    # model: (0.9-1)^2 = 0.01 ; market: (0.5-1)^2 = 0.25
    assert abs(report.overall.model_brier - 0.01) < 1e-9
    assert abs(report.overall.market_brier - 0.25) < 1e-9
    assert report.overall.n_with_market_consensus == 1


def test_unique_events_deduplicate_by_player_and_match_not_by_row(db_session):
    match, player = _seed_match_and_player(db_session)
    # Same player/match, two different thresholds -> one unique event, two settled rows.
    db_session.add(_snapshot(match, player, model_probability=0.6, outcome="won", threshold=20.5))
    db_session.add(_snapshot(match, player, model_probability=0.3, outcome="lost", threshold=30.5))
    db_session.commit()

    report = load_prospective_evaluation(db_session)
    assert report.n_settled == 2
    assert report.n_unique_player_match_events == 1


def test_split_by_market_family_and_probability_bucket(db_session):
    match, player = _seed_match_and_player(db_session)
    db_session.add(_snapshot(match, player, model_probability=0.55, outcome="won", market_family="player_disposals", threshold=20.5))
    db_session.add(_snapshot(match, None, model_probability=0.85, outcome="won", market_family="team", market_type="h2h", threshold=None))
    db_session.commit()

    report = load_prospective_evaluation(db_session)
    families = {s.label for s in report.by_market_family}
    assert families == {"player_disposals", "team"}
    buckets = {s.label for s in report.by_probability_bucket}
    assert "50-60%" in buckets
    assert "80%+" in buckets


def test_dataset_never_reads_historical_backtest_tables(db_session):
    """Sanity: an empty PricingSnapshot table must produce the
    accumulating state even if historical PlayerModelValidationMetric
    rows exist elsewhere — this module must never fall back to backtest
    data to fill a gap."""
    from app.models import PlayerModelRun, PlayerModelValidationMetric

    run = PlayerModelRun(
        model_name="disposals_huber", market="player_disposals", feature_names=[], config_json={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
        evaluation_end_year=2025, is_promoted=True, run_at=NOW,
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(PlayerModelValidationMetric(model_run_id=run.id, segment="overall", metric_name="mae", n=74197, value=3.907))
    db_session.commit()

    report = load_prospective_evaluation(db_session)
    assert report.has_settled_data is False
    assert report.n_settled == 0
