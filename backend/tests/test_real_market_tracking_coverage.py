"""Tests for observation coverage metrics and market-open timing (Sections
10-11, 20 of the live-operations stage brief), plus a structural check that
this reporting module can never be used to retune the model (Section 17)."""

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import Bookmaker, Match, MatchStatus, Player, PropMarketObservation, Round, Season, Sport, Team
from app.player_modelling.real_market_tracking import coverage_metrics, market_open_timing

KICKOFF = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _seed(db):
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
        scheduled_start=KICKOFF, status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db.add_all([player, bookmaker])
    db.commit()
    return match, player, bookmaker


def _obs(match, player, bookmaker, *, odds, observed_at, threshold=29.5, source="the_odds_api"):
    return PropMarketObservation(
        quote_id=1, match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id,
        market_type="player_disposals", line_type="over_under", threshold=threshold, source=source,
        offered_odds=odds, observed_at=observed_at, raw_implied_probability=1 / odds,
        devigged_probability=None, overround_removed=False,
        model_probability=0.55, model_fair_odds=1.82, predicted_mean=28.0,
        model_name="disposals_nb", model_version="v1", data_cutoff=observed_at,
        confidence_tier="moderate_confidence", selection_status_at_observation="placeholder",
        is_confirmed_at_observation=False, difference_pp=0.02, expected_value=0.045,
    )


def test_coverage_metrics_reports_raw_quotes_and_frozen_observations_separately(db_session):
    from app.models import PlayerPropMarket

    match, player, bookmaker = _seed(db_session)
    t1 = KICKOFF - timedelta(days=1)
    # Two raw automated quotes, but only one gets an observation (simulates
    # the real skipped_no_projection case - the second quote here just
    # never had a PropMarketObservation created for it).
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=29.5, selection="over", price_decimal=1.9, recorded_at=t1, source="the_odds_api",
    ))
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=31.5, selection="over", price_decimal=2.1, recorded_at=t1, source="the_odds_api",
    ))
    observations = [_obs(match, player, bookmaker, odds=1.9, observed_at=t1)]
    db_session.add_all(observations)
    db_session.commit()

    metrics = coverage_metrics(db_session, observations)

    assert metrics.total_raw_quotes == 2
    assert metrics.frozen_observations == 1
    assert metrics.bookmakers == ["SportsBet"]
    assert metrics.market_families == ["player_disposals"]


def test_coverage_metrics_excludes_manual_quotes_from_raw_count(db_session):
    from app.models import PlayerPropMarket

    match, player, bookmaker = _seed(db_session)
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=29.5, selection="over", price_decimal=1.9,
        recorded_at=KICKOFF - timedelta(days=1), source="manual",
    ))
    db_session.commit()

    metrics = coverage_metrics(db_session, [])

    assert metrics.total_raw_quotes == 0


def test_average_snapshots_per_player_market(db_session):
    match, player, bookmaker = _seed(db_session)
    t1 = KICKOFF - timedelta(days=2)
    t2 = KICKOFF - timedelta(days=1)
    # Same (player, match, bookmaker, market, line, threshold) key observed
    # twice = 2 snapshots for 1 distinct player-market line.
    observations = [
        _obs(match, player, bookmaker, odds=1.9, observed_at=t1, threshold=29.5),
        _obs(match, player, bookmaker, odds=1.8, observed_at=t2, threshold=29.5),
    ]
    db_session.add_all(observations)
    db_session.commit()

    metrics = coverage_metrics(db_session, observations)

    assert metrics.average_snapshots_per_player_market == 2.0


def test_coverage_metrics_average_is_none_when_no_observations(db_session):
    _seed(db_session)
    metrics = coverage_metrics(db_session, [])
    assert metrics.average_snapshots_per_player_market is None


def test_market_open_timing_first_and_latest(db_session):
    match, player, bookmaker = _seed(db_session)
    t1 = KICKOFF - timedelta(hours=100)
    t2 = KICKOFF - timedelta(hours=5)
    observations = [
        _obs(match, player, bookmaker, odds=1.9, observed_at=t1),
        _obs(match, player, bookmaker, odds=1.7, observed_at=t2),
    ]
    db_session.add_all(observations)
    db_session.commit()

    timing = market_open_timing(db_session, observations)

    assert len(timing) == 1
    t = timing[0]
    assert t.player_name == "Nick Daicos"
    assert t.bookmaker_name == "SportsBet"
    assert abs(t.first_hours_before_kickoff - 100.0) < 0.01
    assert abs(t.latest_hours_before_kickoff - 5.0) < 0.01
    assert t.n_observations == 2


def test_market_open_timing_price_changes_counts_distinct_odds_only(db_session):
    match, player, bookmaker = _seed(db_session)
    t1 = KICKOFF - timedelta(hours=50)
    t2 = KICKOFF - timedelta(hours=40)
    t3 = KICKOFF - timedelta(hours=30)
    observations = [
        _obs(match, player, bookmaker, odds=1.9, observed_at=t1),
        _obs(match, player, bookmaker, odds=1.9, observed_at=t2),  # unchanged re-observation - not a price change
        _obs(match, player, bookmaker, odds=1.8, observed_at=t3),  # genuine change
    ]
    db_session.add_all(observations)
    db_session.commit()

    timing = market_open_timing(db_session, observations)

    assert timing[0].n_price_changes == 2
    assert timing[0].n_observations == 3


def test_market_open_timing_grouped_separately_per_threshold(db_session):
    match, player, bookmaker = _seed(db_session)
    t1 = KICKOFF - timedelta(hours=10)
    observations = [
        _obs(match, player, bookmaker, odds=1.9, observed_at=t1, threshold=27.5),
        _obs(match, player, bookmaker, odds=2.5, observed_at=t1, threshold=31.5),
    ]
    db_session.add_all(observations)
    db_session.commit()

    timing = market_open_timing(db_session, observations)

    assert len(timing) == 2
    assert {t.threshold for t in timing} == {27.5, 31.5}


def test_real_market_tracking_module_never_imports_model_training_code():
    """Section 17: structural guard - this reporting layer must be
    physically incapable of feeding back into model training/tuning code,
    not just documented as off-limits. Parses the module's own imports
    rather than grepping the module's prose, so it can't be fooled by a
    comment."""
    module_path = Path(__file__).resolve().parents[1] / "app" / "player_modelling" / "real_market_tracking.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)

    forbidden_substrings = ["disposal_models", "goal_models", "disposal_confidence", "goal_confidence", "prop_opportunity_ranking"]
    for mod in imported_modules:
        for forbidden in forbidden_substrings:
            assert forbidden not in mod, f"real_market_tracking.py must not import {mod} (touches model/ranking code)"
