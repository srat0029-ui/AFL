"""Tests for the Live Status read-model (Sections 5-6, 18, 20 of the
live-operations stage brief): round summary aggregation, per-match
coverage/simple-status derivation, and operational run history - all
read-only, never triggering an external request."""

from datetime import datetime, timedelta, timezone

from app.models import (
    ExpectedLineup,
    LiveCycleRun,
    Match,
    MatchStatus,
    Player,
    PlayerPropMarket,
    PropMarketObservation,
    Round,
    SelectionStatus,
    Season,
    Sport,
    Team,
    derive_coarse_status,
)
from app.player_modelling.live_status import (
    STATUS_COMPLETED_AWAITING_STATS,
    STATUS_READY,
    STATUS_SETTLED,
    STATUS_WAITING_FOR_BOOKMAKER_MARKETS,
    STATUS_WAITING_FOR_TEAMS,
    load_live_status,
)

KICKOFF = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _seed_match(db, *, status=MatchStatus.SCHEDULED):
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
        scheduled_start=KICKOFF, status=status,
    )
    db.add(match)
    db.commit()
    return match, home, away


def test_round_summary_reflects_zero_state_for_a_fresh_match(db_session):
    _seed_match(db_session)
    report = load_live_status(db_session)

    assert report.round_summary.n_upcoming_matches == 1
    assert report.round_summary.n_matches_with_projections == 0
    assert report.round_summary.n_matches_with_bookmaker_events == 0
    assert report.round_summary.n_real_quotes_stored == 0
    assert report.round_summary.n_confirmed_lineups == 0
    assert report.round_summary.n_placeholder_or_uncertain_lineups == 1


def test_match_status_waiting_for_teams_when_lineup_not_confirmed(db_session):
    _seed_match(db_session)
    report = load_live_status(db_session)
    assert report.matches[0].simple_status == STATUS_WAITING_FOR_TEAMS


def test_match_status_waiting_for_bookmaker_markets_once_lineup_confirmed(db_session):
    match, home, away = _seed_match(db_session)
    player = Player(sport_id=match.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    db_session.add(player)
    db_session.flush()
    db_session.add(ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=home.id,
        status=derive_coarse_status(SelectionStatus.CONFIRMED_SELECTED.value), selection_status=SelectionStatus.CONFIRMED_SELECTED.value,
        is_confirmed=True, recorded_at=datetime.now(timezone.utc), source="manual",
    ))
    db_session.commit()

    report = load_live_status(db_session)
    assert report.matches[0].simple_status == STATUS_WAITING_FOR_BOOKMAKER_MARKETS
    assert report.round_summary.n_confirmed_lineups == 1


def test_match_status_ready_when_lineup_confirmed_odds_fresh_and_projected(db_session):
    from app.models import Bookmaker, PlayerDisposalProjection

    match, home, away = _seed_match(db_session)
    match.external_ids = {"the_odds_api": "evt1"}
    player = Player(sport_id=match.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db_session.add_all([player, bookmaker])
    db_session.flush()
    db_session.add(ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=home.id,
        status="expected_in", selection_status=SelectionStatus.CONFIRMED_SELECTED.value,
        is_confirmed=True, recorded_at=datetime.now(timezone.utc), source="manual",
    ))
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=29.5, selection="over", price_decimal=1.9,
        recorded_at=datetime.now(timezone.utc), source="the_odds_api",  # fresh - within the match-time-aware interval
    ))
    db_session.add(PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=home.id, model_name="disposals_nb", model_version="v1",
        generated_at=datetime.now(timezone.utc), data_cutoff=datetime.now(timezone.utc), lineup_status_at_generation="expected_in",
        games_of_history=20, predicted_mean=28.0, distribution_method="nb", nb_alpha=8.0, confidence_tier="moderate_confidence",
    ))
    db_session.commit()

    report = load_live_status(db_session)
    assert report.matches[0].simple_status == STATUS_READY
    assert report.matches[0].projections_generated is True
    assert report.round_summary.n_matches_with_projections == 1
    assert report.round_summary.n_matches_with_prop_markets == 1
    assert report.matches[0].disposals_available is True
    assert report.matches[0].goals_available is False
    assert report.matches[0].unique_player_count == 1


def test_completed_match_with_unsettled_observation_is_awaiting_stats(db_session):
    from app.models import Bookmaker

    match, home, away = _seed_match(db_session, status=MatchStatus.COMPLETED)
    player = Player(sport_id=match.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db_session.add_all([player, bookmaker])
    db_session.flush()
    db_session.add(PropMarketObservation(
        quote_id=1, match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id,
        market_type="player_disposals", line_type="over_under", threshold=29.5, source="the_odds_api",
        offered_odds=1.9, observed_at=datetime.now(timezone.utc), raw_implied_probability=0.526,
        devigged_probability=None, overround_removed=False, model_probability=0.55, model_fair_odds=1.82,
        predicted_mean=28.0, model_name="disposals_nb", model_version="v1", data_cutoff=datetime.now(timezone.utc),
        confidence_tier="moderate_confidence", selection_status_at_observation="placeholder",
        is_confirmed_at_observation=False, difference_pp=0.02, expected_value=0.045,
    ))
    db_session.commit()

    report = load_live_status(db_session)
    # A completed match has no bearing on "upcoming round" scope (which is
    # SCHEDULED-only) - this test exercises load_match_coverage directly
    # via the module's own helper instead, since load_live_status only
    # walks the upcoming round.
    from app.player_modelling.live_status import load_match_coverage
    from app.player_modelling.upcoming_features import UpcomingMatchTeams

    fake_upcoming = UpcomingMatchTeams(
        match_id=match.id, home_team_id=home.id, away_team_id=away.id, venue_id=None,
        scheduled_start=match.scheduled_start, season_year=2026, round_number=1, is_final=False,
    )
    coverage = load_match_coverage(db_session, fake_upcoming)
    assert coverage.simple_status == STATUS_COMPLETED_AWAITING_STATS
    assert coverage.n_observations_awaiting_settlement == 1


def test_live_cycle_run_history_is_returned_most_recent_first(db_session):
    _seed_match(db_session)
    older = LiveCycleRun(
        run_at=datetime.now(timezone.utc) - timedelta(hours=2), finished_at=datetime.now(timezone.utc) - timedelta(hours=2),
        overall_status="ok", steps=[], matches_affected=1, quotes_added=0, observations_added=0, observations_settled=0,
    )
    newer = LiveCycleRun(
        run_at=datetime.now(timezone.utc) - timedelta(minutes=5), finished_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        overall_status="partial", steps=[{"step": "x", "status": "warning", "detail": "y"}],
        matches_affected=1, quotes_added=3, observations_added=2, observations_settled=0,
    )
    db_session.add_all([older, newer])
    db_session.commit()

    report = load_live_status(db_session)

    assert len(report.recent_runs) == 2
    assert report.recent_runs[0].id == newer.id
    assert report.recent_runs[1].id == older.id
