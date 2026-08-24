"""Targeted tests for match-day readiness (Finals Multi Quality + Match-Day
Readiness stage, item 12): NOT_READY / PROVISIONAL / READY derivation from
already-computed signals only."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    Bookmaker, ExpectedLineup, GoalModelRun, Match, MatchStatus, OddsQuote, Player, PlayerDisposalProjection,
    PlayerGoalProjection, PlayerModelRun, PlayerPropMarket, Round, Season, Sport, Team,
)
from app.player_modelling.market import PlayerMarket
from app.player_modelling.match_readiness import NOT_READY, PROVISIONAL, READY, compute_match_readiness

NOW = datetime.now(timezone.utc)


def _seed_match(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=25, name="Wildcard Finals")
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=2), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _add_fresh_team_odds(db, match, selection):
    bookmaker = Bookmaker(name="SportsBet")
    db.add(bookmaker)
    db.flush()
    db.add(OddsQuote(
        match_id=match.id, bookmaker_id=bookmaker.id, market_type="h2h", selection=selection,
        line_value=None, price_decimal=1.8, recorded_at=NOW, source="manual", is_closing_line=False,
    ))
    db.commit()
    return bookmaker


def _add_player_with_projection(db, match, team, bookmaker, *, confirmed):
    player = Player(sport_id=match.sport_id, display_name="Test Player", source="afltables", source_player_id="rp1", current_team_id=team.id)
    db.add(player)
    disposal_run = PlayerModelRun(
        model_name="disposals_test", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
        evaluation_end_year=2025, is_promoted=True, run_at=NOW,
    )
    goal_run = GoalModelRun(
        model_name="goals_test", market=PlayerMarket.GOALS.value, feature_names=[], config_json={},
        distribution_kind="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
        evaluation_end_year=2025, is_promoted=True, run_at=NOW,
    )
    db.add_all([disposal_run, goal_run])
    db.commit()
    # model_version must match current_disposal/goal_model_version(db)'s own
    # computed string EXACTLY, sourced by re-querying it the same way that
    # function does (SQLite drops tzinfo across a round-trip, so the
    # in-memory run_at.isoformat() before commit is not reliably identical
    # to what a fresh query returns after) - otherwise live_change_detection's
    # check_staleness (reused unchanged by compute_match_readiness) always
    # sees a version mismatch and reports the projection as needing
    # regeneration regardless of how fresh it actually is.
    from app.player_modelling.live_report_query import current_disposal_model_version, current_goal_model_version

    disposal_version = current_disposal_model_version(db)
    goal_version = current_goal_model_version(db)
    db.add(PlayerDisposalProjection(
        match_id=match.id, player_id=player.id, team_id=team.id, model_name="disposals_test",
        model_version=disposal_version,
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=22.0, distribution_method="nb", nb_alpha=0.3, confidence_tier="higher_confidence",
        warnings=[], input_features={},
    ))
    # detect_matches_needing_regeneration (reused unchanged by
    # compute_match_readiness) requires EVERY expected player to have BOTH
    # a disposal AND a goal projection, or it reports the match as needing
    # regeneration regardless of freshness.
    db.add(PlayerGoalProjection(
        match_id=match.id, player_id=player.id, team_id=team.id, model_name="goals_test",
        model_version=goal_version,
        generated_at=NOW, data_cutoff=NOW, lineup_status_at_generation="expected_in", games_of_history=40,
        predicted_mean=1.5, distribution_kind="nb", nb_alpha=0.8, scoring_archetype="forward",
        confidence_tier="higher_confidence", warnings=[], input_features={},
    ))
    db.add(ExpectedLineup(
        match_id=match.id, player_id=player.id, team_id=team.id, status="expected_in",
        selection_status="confirmed_selected" if confirmed else "uncertain", is_confirmed=confirmed,
        recorded_at=NOW, source="manual",
    ))
    # threshold well under the mean -> genuinely high model probability, so
    # a still-generous bookmaker price nets a POSITIVE model-market edge
    # (opportunities_only=True in the underlying pipeline silently drops
    # negative-edge rows - same requirement test_multi_builder.py's own
    # _add_player_leg documents).
    db.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type=PlayerMarket.DISPOSALS.value,
        line_type="over_under", threshold=10.5, selection="over", price_decimal=1.6, recorded_at=NOW, source="the_odds_api",
    ))
    db.commit()
    return player


def test_not_ready_when_no_markets_and_no_projections_exist(db_session):
    match, home, away = _seed_match(db_session)
    readiness = compute_match_readiness(db_session, match.id)
    assert readiness.state == NOT_READY
    assert not readiness.has_fresh_odds
    assert not readiness.has_projections
    assert readiness.reasons  # never silent


def test_not_ready_when_odds_are_stale(db_session):
    match, home, away = _seed_match(db_session)
    bookmaker = Bookmaker(name="SportsBet")
    db_session.add(bookmaker)
    db_session.flush()
    db_session.add(OddsQuote(
        match_id=match.id, bookmaker_id=bookmaker.id, market_type="h2h", selection=home.name,
        line_value=None, price_decimal=1.8, recorded_at=NOW - timedelta(days=5), source="manual", is_closing_line=False,
    ))
    db_session.commit()

    readiness = compute_match_readiness(db_session, match.id)
    assert readiness.state == NOT_READY
    assert not readiness.has_fresh_odds


def test_provisional_when_fresh_markets_and_projections_but_teams_unconfirmed(db_session):
    match, home, away = _seed_match(db_session)
    bookmaker = _add_fresh_team_odds(db_session, match, home.name)
    _add_player_with_projection(db_session, match, home, bookmaker, confirmed=False)

    readiness = compute_match_readiness(db_session, match.id)
    assert readiness.state == PROVISIONAL
    assert readiness.has_fresh_odds
    assert readiness.has_projections
    assert not readiness.teams_confirmed


def test_ready_when_everything_current_and_confirmed(db_session):
    match, home, away = _seed_match(db_session)
    bookmaker = _add_fresh_team_odds(db_session, match, home.name)
    _add_player_with_projection(db_session, match, home, bookmaker, confirmed=True)

    readiness = compute_match_readiness(db_session, match.id)
    assert readiness.state == READY
    assert readiness.teams_confirmed
    assert readiness.projections_current
    assert readiness.reasons == []
