"""Tests for prop settlement (Sections 7-8, 16, 23 of the market-logging
stage brief): disposal/goal settlement against real PlayerMatchStat rows,
arbitrary thresholds, push/no-push semantics per line type, unresolved
player handling (never settle without a real result), idempotent rerun,
and actual-result linking without disturbing the frozen quote/model fields."""

from datetime import datetime, timedelta, timezone

from app.models import (
    Bookmaker,
    Match,
    MatchStatus,
    Player,
    PlayerMatchStat,
    PropMarketObservation,
    Round,
    Season,
    Sport,
    Team,
)
from app.player_modelling.prop_settlement import (
    RESULT_LOST,
    RESULT_PUSH,
    RESULT_UNRESOLVED,
    RESULT_VOID,
    RESULT_WON,
    SettlementReport,
    settle_all_completed_matches,
    settle_match_observations,
    settle_observation,
)

NOW = datetime.now(timezone.utc)


def _seed_match(db, status=MatchStatus.COMPLETED):
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
        scheduled_start=NOW - timedelta(days=1), status=status,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db.add_all([player, bookmaker])
    db.commit()
    return match, home, away, player, bookmaker


def _observation(match, player, bookmaker, *, market_type="player_disposals", line_type="over_under", threshold=29.5):
    return PropMarketObservation(
        quote_id=1, match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id,
        market_type=market_type, line_type=line_type, threshold=threshold, source="the_odds_api",
        offered_odds=1.9, observed_at=NOW - timedelta(hours=5), raw_implied_probability=0.526,
        devigged_probability=None, overround_removed=False,
        model_probability=0.55, model_fair_odds=1.82, predicted_mean=28.0,
        model_name="disposals_nb", model_version="v1", data_cutoff=NOW - timedelta(hours=5),
        confidence_tier="moderate_confidence", selection_status_at_observation="placeholder",
        is_confirmed_at_observation=False, difference_pp=0.024, expected_value=0.045,
    )


def test_disposal_over_settles_won_when_actual_clears_threshold(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30, goals=0,
    ))
    obs = _observation(match, player, bookmaker, threshold=29.5)
    db_session.add(obs)
    db_session.commit()

    settle_observation(db_session, obs, SettlementReport())
    db_session.commit()

    assert obs.market_result == RESULT_WON
    assert obs.actual_stat_value == 30
    assert obs.settled_at is not None


def test_disposal_over_settles_lost_when_actual_below_threshold(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=25, goals=0,
    ))
    obs = _observation(match, player, bookmaker, threshold=29.5)
    db_session.add(obs)
    db_session.commit()

    settle_observation(db_session, obs, SettlementReport())

    assert obs.market_result == RESULT_LOST
    assert obs.actual_stat_value == 25


def test_over_under_whole_number_threshold_pushes_on_exact_match(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30, goals=0,
    ))
    obs = _observation(match, player, bookmaker, threshold=30.0)  # whole-number line, arbitrary threshold
    db_session.add(obs)
    db_session.commit()

    settle_observation(db_session, obs, SettlementReport())

    assert obs.market_result == RESULT_PUSH


def test_arbitrary_half_line_threshold_settles_correctly(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=18, goals=0,
    ))
    obs = _observation(match, player, bookmaker, threshold=17.5)
    db_session.add(obs)
    db_session.commit()

    settle_observation(db_session, obs, SettlementReport())

    assert obs.market_result == RESULT_WON


def test_multi_plus_line_has_no_push_case(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=0, goals=2,
    ))
    obs = _observation(match, player, bookmaker, market_type="player_goals", line_type="multi_plus", threshold=2.0)
    db_session.add(obs)
    db_session.commit()

    settle_observation(db_session, obs, SettlementReport())

    assert obs.market_result == RESULT_WON  # exact-boundary N+ wins outright, never a push


def test_multi_plus_line_loses_below_threshold(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=0, goals=1,
    ))
    obs = _observation(match, player, bookmaker, market_type="player_goals", line_type="multi_plus", threshold=2.0)
    db_session.add(obs)
    db_session.commit()

    settle_observation(db_session, obs, SettlementReport())

    assert obs.market_result == RESULT_LOST


def test_no_stats_ingested_for_match_at_all_leaves_observation_pending(db_session):
    """Section 14: match complete but NO player has a PlayerMatchStat row
    yet for it at all - stats simply haven't been ingested, so this must be
    left pending ("awaiting player stats"), never voided just because the
    data hasn't arrived."""
    match, home, away, player, bookmaker = _seed_match(db_session)
    obs = _observation(match, player, bookmaker)
    db_session.add(obs)
    db_session.commit()

    report = SettlementReport()
    settle_observation(db_session, obs, report)

    assert obs.market_result is None
    assert obs.settled_at is None
    assert report.awaiting_player_stats == 1
    assert report.observations_settled == 0


def test_genuine_dnp_settles_void_when_other_players_have_stats(db_session):
    """Distinguishes the awaiting-stats case above from a real DNP: once
    ingestion has clearly run for this match (some OTHER player already has
    a PlayerMatchStat row), this player's continued absence means they
    didn't play, not that data hasn't arrived — that's a legitimate void."""
    match, home, away, player, bookmaker = _seed_match(db_session)
    other_player = Player(sport_id=player.sport_id, display_name="Someone Else", source="afltables", source_player_id="p2", current_team_id=home.id)
    db_session.add(other_player)
    db_session.flush()
    db_session.add(PlayerMatchStat(
        player_id=other_player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=20, goals=1,
    ))
    obs = _observation(match, player, bookmaker)
    db_session.add(obs)
    db_session.commit()

    report = SettlementReport()
    settle_observation(db_session, obs, report)

    assert obs.market_result == RESULT_VOID
    assert obs.settled_at is not None
    assert obs.actual_stat_value is None
    assert report.awaiting_player_stats == 0
    assert report.observations_voided == 1


def test_negative_actual_stat_flagged_for_review_not_silently_settled(db_session):
    """Section 15: result verification - an impossible actual value (a
    negative disposal count) must be flagged for manual review, not settled
    as an ordinary loss."""
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=-1, goals=0,
    ))
    obs = _observation(match, player, bookmaker, threshold=29.5)
    db_session.add(obs)
    db_session.commit()

    report = SettlementReport()
    settle_observation(db_session, obs, report)

    assert obs.market_result == RESULT_UNRESOLVED
    assert obs.needs_review is True
    assert obs.review_reason is not None
    assert report.observations_flagged_for_review == 1


def test_stat_row_with_null_stat_value_settles_unresolved(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=None, goals=0,
    ))
    obs = _observation(match, player, bookmaker, threshold=29.5)
    db_session.add(obs)
    db_session.commit()

    settle_observation(db_session, obs, SettlementReport())

    assert obs.market_result == RESULT_UNRESOLVED
    assert obs.actual_stat_value is None


def test_already_settled_observation_is_not_resettled(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30, goals=0,
    ))
    obs = _observation(match, player, bookmaker, threshold=29.5)
    db_session.add(obs)
    db_session.commit()

    report1 = SettlementReport()
    settle_observation(db_session, obs, report1)
    db_session.commit()
    first_settled_at = obs.settled_at

    report2 = SettlementReport()
    settle_observation(db_session, obs, report2)

    assert report2.already_settled_skipped == 1
    assert report2.observations_settled == 0
    assert obs.settled_at == first_settled_at  # untouched, not re-stamped


def test_settlement_never_touches_frozen_quote_or_model_fields(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30, goals=0,
    ))
    obs = _observation(match, player, bookmaker, threshold=29.5)
    db_session.add(obs)
    db_session.commit()

    frozen_before = (obs.offered_odds, obs.model_probability, obs.predicted_mean, obs.model_version, obs.confidence_tier)
    settle_observation(db_session, obs, SettlementReport())
    frozen_after = (obs.offered_odds, obs.model_probability, obs.predicted_mean, obs.model_version, obs.confidence_tier)

    assert frozen_before == frozen_after


def test_settle_match_observations_no_ops_for_scheduled_match(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session, status=MatchStatus.SCHEDULED)
    obs = _observation(match, player, bookmaker)
    db_session.add(obs)
    db_session.commit()

    report = settle_match_observations(db_session, match.id)

    assert report.matches_not_completed == [match.id]
    assert obs.settled_at is None


def test_settle_match_observations_idempotent_rerun(db_session):
    match, home, away, player, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30, goals=0,
    ))
    obs = _observation(match, player, bookmaker, threshold=29.5)
    db_session.add(obs)
    db_session.commit()

    report1 = settle_match_observations(db_session, match.id)
    report2 = settle_match_observations(db_session, match.id)

    assert report1.observations_settled == 1
    assert report2.observations_settled == 0  # nothing left unsettled on rerun
    assert db_session.query(PropMarketObservation).filter(PropMarketObservation.settled_at.isnot(None)).count() == 1


def test_settle_all_completed_matches_scoped_to_matches_with_unsettled_observations(db_session):
    match1, home1, away1, player1, bookmaker = _seed_match(db_session)
    db_session.add(PlayerMatchStat(
        player_id=player1.id, match_id=match1.id, team_id=home1.id, source="afltables", recorded_at=NOW, disposals=30, goals=0,
    ))
    obs1 = _observation(match1, player1, bookmaker, threshold=29.5)
    db_session.add(obs1)

    # Second completed match with no observations at all - must not error or be counted.
    sport = db_session.query(Sport).one()
    season = db_session.query(Season).one()
    round_ = db_session.query(Round).one()
    home2 = Team(sport_id=sport.id, name="Essendon", short_name="ESS")
    away2 = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db_session.add_all([home2, away2])
    db_session.flush()
    match2 = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home2.id, away_team_id=away2.id,
        scheduled_start=NOW - timedelta(days=1), status=MatchStatus.COMPLETED,
    )
    db_session.add(match2)
    db_session.commit()

    combined = settle_all_completed_matches(db_session)

    assert combined.observations_settled == 1
    assert combined.observations_won == 1
    assert obs1.market_result == RESULT_WON
