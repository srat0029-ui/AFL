"""Tests for the real-market-tracking research layer (Sections 9-15, 20, 23
of the market-logging stage brief): pseudo-replication-safe grouped sample
counting, edge/confidence/timing buckets, small-sample framing driven by
SETTLED observations only (not raw/pending rows), and that this reporting
never mixes with the synthetic 2016-2025 backtest datasets (which this
module doesn't import at all)."""

from datetime import datetime, timedelta, timezone

from app.models import Bookmaker, Match, MatchStatus, Player, PropMarketObservation, Round, Season, Sport, Team
from app.player_modelling.real_market_tracking import (
    SAMPLE_EXPLORATORY,
    SAMPLE_LOW_CONFIDENCE,
    dataset_summary,
    edge_buckets,
    load_real_market_tracking_report,
    sample_size_level,
    timing_buckets,
)

KICKOFF = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _seed_match(db, name_suffix="1"):
    sport = db.query(Sport).first()
    if sport is None:
        sport = Sport(code="AFL", name="Australian Football League")
        db.add(sport)
        db.flush()
    season = db.query(Season).first()
    if season is None:
        season = Season(sport_id=sport.id, year=2026)
        db.add(season)
        db.flush()
    round_ = db.query(Round).filter_by(season_id=season.id, round_number=1).first()
    if round_ is None:
        round_ = Round(season_id=season.id, round_number=1)
        db.add(round_)
        db.flush()
    home = Team(sport_id=sport.id, name=f"Home{name_suffix}", short_name=f"H{name_suffix}")
    away = Team(sport_id=sport.id, name=f"Away{name_suffix}", short_name=f"A{name_suffix}")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=KICKOFF, status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name=f"Player{name_suffix}", source="afltables", source_player_id=f"p{name_suffix}", current_team_id=home.id)
    bookmaker = db.query(Bookmaker).filter_by(name="SportsBet").first()
    if bookmaker is None:
        bookmaker = Bookmaker(name="SportsBet")
        db.add(bookmaker)
    db.add(player)
    db.commit()
    return match, player, bookmaker


def _obs(match, player, bookmaker, *, threshold=29.5, diff=0.02, confidence="moderate_confidence",
         result=None, observed_at=None, model_prob=0.55):
    obs = PropMarketObservation(
        quote_id=1, match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id,
        market_type="player_disposals", line_type="over_under", threshold=threshold, source="the_odds_api",
        offered_odds=1.9, observed_at=observed_at or (KICKOFF - timedelta(hours=5)),
        raw_implied_probability=0.526, devigged_probability=None, overround_removed=False,
        model_probability=model_prob, model_fair_odds=1.82, predicted_mean=28.0,
        model_name="disposals_nb", model_version="v1", data_cutoff=KICKOFF - timedelta(hours=5),
        confidence_tier=confidence, selection_status_at_observation="placeholder",
        is_confirmed_at_observation=False, difference_pp=diff, expected_value=0.045,
    )
    if result is not None:
        obs.market_result = result
        obs.actual_stat_value = 30.0 if result == "won" else 25.0
        obs.settled_at = datetime.now(timezone.utc)
    return obs


def test_sample_size_level_thresholds():
    assert sample_size_level(0) == "exploratory"
    assert sample_size_level(29) == "exploratory"
    assert sample_size_level(30) == "low_confidence"
    assert sample_size_level(99) == "low_confidence"
    assert sample_size_level(100) == "still_developing"
    assert sample_size_level(299) == "still_developing"
    assert sample_size_level(300) == "informative"


def test_pseudo_replication_many_lines_same_player_match_count_as_one(db_session):
    """31 alternate disposal-line observations for the SAME player+match are
    not 31 independent pieces of evidence - unique_player_matches must
    report 1, even though total_observations reports 31."""
    match, player, bookmaker = _seed_match(db_session)
    observations = [_obs(match, player, bookmaker, threshold=10.5 + i) for i in range(31)]
    db_session.add_all(observations)
    db_session.commit()

    summary = dataset_summary(db_session.query(PropMarketObservation).all())

    assert summary.total_observations == 31
    assert summary.unique_player_matches == 1
    assert summary.unique_players == 1
    assert summary.unique_matches == 1


def test_sample_size_level_uses_settled_binary_not_pending_count(db_session):
    """A report with 0 settled observations but many pending ones must
    still show 'exploratory' - pending rows carry no win/loss evidence and
    must not inflate the sample-size framing (the bug I caught and fixed
    while building this stage)."""
    match, player, bookmaker = _seed_match(db_session)
    observations = [_obs(match, player, bookmaker, threshold=10.5 + i) for i in range(40)]  # all pending
    db_session.add_all(observations)
    db_session.commit()

    report = load_real_market_tracking_report(db_session)

    assert report.summary.unique_player_matches == 1
    assert report.overall_sample_level == SAMPLE_EXPLORATORY  # not low_confidence, despite 40 raw rows


def test_sample_size_level_counts_only_settled_player_matches_across_matches(db_session):
    for i in range(1, 31):
        match, player, bookmaker = _seed_match(db_session, name_suffix=str(i))
        db_session.add(_obs(match, player, bookmaker, result="won" if i % 2 == 0 else "lost"))
    db_session.commit()

    report = load_real_market_tracking_report(db_session)

    assert report.summary.unique_player_matches == 30
    assert report.overall_sample_level == SAMPLE_LOW_CONFIDENCE  # 30 settled player-matches


def test_edge_buckets_partition_by_difference_pp(db_session):
    match, player, bookmaker = _seed_match(db_session)
    db_session.add_all([
        _obs(match, player, bookmaker, threshold=10.5, diff=-0.02),  # <=0pp
        _obs(match, player, bookmaker, threshold=11.5, diff=0.02),   # 0-3pp
        _obs(match, player, bookmaker, threshold=12.5, diff=0.04),   # 3-5pp
        _obs(match, player, bookmaker, threshold=13.5, diff=0.10),   # 8-12pp
    ])
    db_session.commit()

    buckets = edge_buckets(db_session.query(PropMarketObservation).all())
    by_label = {b.label: b for b in buckets}

    assert by_label["≤0pp"].n_observations == 1
    assert by_label["0-3pp"].n_observations == 1
    assert by_label["3-5pp"].n_observations == 1
    assert by_label["8-12pp"].n_observations == 1
    assert by_label["12pp+"].n_observations == 0


def test_edge_bucket_hit_rate_and_roi_computed_from_settled_only(db_session):
    match, player, bookmaker = _seed_match(db_session)
    db_session.add_all([
        _obs(match, player, bookmaker, threshold=10.5, diff=0.06, result="won"),
        _obs(match, player, bookmaker, threshold=11.5, diff=0.06, result="lost"),
        _obs(match, player, bookmaker, threshold=12.5, diff=0.06),  # pending, excluded from hit rate
    ])
    db_session.commit()

    buckets = {b.label: b for b in edge_buckets(db_session.query(PropMarketObservation).all())}
    bucket_5_8 = buckets["5-8pp"]

    assert bucket_5_8.n_observations == 3
    assert bucket_5_8.returns.n_settled_binary == 2
    assert bucket_5_8.returns.win_rate == 0.5


def test_confidence_buckets_partition_by_tier(db_session):
    match, player, bookmaker = _seed_match(db_session)
    db_session.add_all([
        _obs(match, player, bookmaker, threshold=10.5, confidence="higher_confidence"),
        _obs(match, player, bookmaker, threshold=11.5, confidence="insufficient_history"),
    ])
    db_session.commit()

    report = load_real_market_tracking_report(db_session)
    by_tier = {b.label: b for b in report.confidence_buckets}

    assert by_tier["higher_confidence"].n_observations == 1
    assert by_tier["insufficient_history"].n_observations == 1
    assert by_tier["moderate_confidence"].n_observations == 0


def test_timing_buckets_use_hours_before_kickoff(db_session):
    match, player, bookmaker = _seed_match(db_session)
    db_session.add_all([
        _obs(match, player, bookmaker, threshold=10.5, observed_at=KICKOFF - timedelta(hours=72)),  # 48h+
        _obs(match, player, bookmaker, threshold=11.5, observed_at=KICKOFF - timedelta(hours=3)),    # 1-6h
        _obs(match, player, bookmaker, threshold=12.5, observed_at=KICKOFF - timedelta(minutes=30)), # <1h
    ])
    db_session.commit()

    buckets = {b.label: b for b in timing_buckets(db_session, db_session.query(PropMarketObservation).all())}

    assert buckets["48h+"].n_observations == 1
    assert buckets["1-6h"].n_observations == 1
    assert buckets["<1h"].n_observations == 1
    assert buckets["24-48h"].n_observations == 0


def test_report_filters_by_match_id(db_session):
    match1, player1, bookmaker = _seed_match(db_session, name_suffix="1")
    match2, player2, _ = _seed_match(db_session, name_suffix="2")
    db_session.add_all([
        _obs(match1, player1, bookmaker, threshold=10.5),
        _obs(match2, player2, bookmaker, threshold=11.5),
    ])
    db_session.commit()

    report = load_real_market_tracking_report(db_session, match_id=match1.id)

    assert report.summary.total_observations == 1
    assert report.summary.unique_matches == 1


def test_report_label_identifies_real_logged_data_distinct_from_synthetic_backtest(db_session):
    match, player, bookmaker = _seed_match(db_session)
    db_session.add(_obs(match, player, bookmaker))
    db_session.commit()

    report = load_real_market_tracking_report(db_session)

    assert report.label == "Real logged market observations"


def test_actual_result_linking_does_not_affect_dataset_summary_shape(db_session):
    match, player, bookmaker = _seed_match(db_session)
    db_session.add(_obs(match, player, bookmaker, result="won"))
    db_session.commit()

    summary = dataset_summary(db_session.query(PropMarketObservation).all())

    assert summary.settled_observations == 1
    assert summary.pending_observations == 0
