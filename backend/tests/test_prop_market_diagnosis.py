"""Tests for missing-market diagnosis (Section 12, 20 of the
live-operations stage brief): distinguishing the different real reasons an
upcoming match currently shows no prop odds, rather than collapsing them
all into one "No odds" status."""

from datetime import datetime, timedelta, timezone

from app.models import Bookmaker, Match, MatchStatus, Player, PlayerPropMarket, Round, Season, Sport, Team
from app.player_modelling.prop_market_diagnosis import (
    DIAG_BOOKMAKER_ABSENT,
    DIAG_DISPOSAL_MARKET_ABSENT,
    DIAG_EVENT_ABSENT,
    DIAG_EVENT_NO_PROPS,
    DIAG_NOT_YET_REFRESHED,
    DIAG_ODDS_AVAILABLE,
    diagnose_match_market_coverage,
)
from app.providers.types import ProviderEvent

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
    db.commit()
    return match, home, away


def test_not_yet_refreshed_when_never_checked(db_session):
    match, home, away = _seed(db_session)
    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=None)
    assert diagnosis.category == DIAG_NOT_YET_REFRESHED


def test_event_absent_when_freshly_checked_and_no_matching_event(db_session):
    match, home, away = _seed(db_session)
    other_event = ProviderEvent(
        provider="the_odds_api", event_id="evt-other", sport_key="aussierules_afl",
        home_team="Some Other Team", away_team="Another Team", commence_time=KICKOFF,
    )
    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=[other_event])
    assert diagnosis.category == DIAG_EVENT_ABSENT


def test_event_no_props_when_resolved_but_zero_quotes(db_session):
    match, home, away = _seed(db_session)
    match.external_ids = {"the_odds_api": "evt1"}
    db_session.commit()

    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=None)
    assert diagnosis.category == DIAG_EVENT_NO_PROPS


def test_disposal_market_absent_when_only_other_markets_quoted(db_session):
    match, home, away = _seed(db_session)
    match.external_ids = {"the_odds_api": "evt1"}
    player = Player(sport_id=match.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db_session.add_all([player, bookmaker])
    db_session.flush()
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_goal_scorer_anytime",
        line_type="multi_plus", threshold=1.0, selection="yes", price_decimal=2.5,
        recorded_at=KICKOFF - timedelta(days=1), source="the_odds_api",
    ))
    db_session.commit()

    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=None)
    assert diagnosis.category == DIAG_DISPOSAL_MARKET_ABSENT


def test_bookmaker_absent_when_expected_bookmaker_missing(db_session):
    match, home, away = _seed(db_session)
    match.external_ids = {"the_odds_api": "evt1"}
    player = Player(sport_id=match.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    other_bookmaker = Bookmaker(name="TAB")
    db_session.add_all([player, other_bookmaker])
    db_session.flush()
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=other_bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=29.5, selection="over", price_decimal=1.9,
        recorded_at=KICKOFF - timedelta(days=1), source="the_odds_api",
    ))
    db_session.commit()

    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=None, expected_bookmaker_name="SportsBet")
    assert diagnosis.category == DIAG_BOOKMAKER_ABSENT


def test_odds_available_when_expected_bookmaker_has_disposal_quotes(db_session):
    match, home, away = _seed(db_session)
    match.external_ids = {"the_odds_api": "evt1"}
    player = Player(sport_id=match.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db_session.add_all([player, bookmaker])
    db_session.flush()
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=29.5, selection="over", price_decimal=1.9,
        recorded_at=KICKOFF - timedelta(days=1), source="the_odds_api",
    ))
    db_session.commit()

    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=None, expected_bookmaker_name="SportsBet")
    assert diagnosis.category == DIAG_ODDS_AVAILABLE


def test_bookmaker_absent_check_skipped_when_expected_bookmaker_is_none(db_session):
    match, home, away = _seed(db_session)
    match.external_ids = {"the_odds_api": "evt1"}
    player = Player(sport_id=match.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="TAB")
    db_session.add_all([player, bookmaker])
    db_session.flush()
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=29.5, selection="over", price_decimal=1.9,
        recorded_at=KICKOFF - timedelta(days=1), source="the_odds_api",
    ))
    db_session.commit()

    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=None, expected_bookmaker_name=None)
    assert diagnosis.category == DIAG_ODDS_AVAILABLE


def test_would_be_skipped_and_hours_to_kickoff_always_computed(db_session):
    match, home, away = _seed(db_session)
    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=None)
    assert diagnosis.hours_to_kickoff > 0
    assert isinstance(diagnosis.would_be_skipped_this_cycle, bool)


def test_market_availability_split_when_both_markets_quoted(db_session):
    match, home, away = _seed(db_session)
    match.external_ids = {"the_odds_api": "evt1"}
    p1 = Player(sport_id=match.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    p2 = Player(sport_id=match.sport_id, display_name="Charlie Curnow", source="afltables", source_player_id="p2", current_team_id=away.id)
    bookmaker = Bookmaker(name="SportsBet")
    db_session.add_all([p1, p2, bookmaker])
    db_session.flush()
    db_session.add_all([
        PlayerPropMarket(
            match_id=match.id, player_id=p1.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
            line_type="over_under", threshold=29.5, selection="over", price_decimal=1.9,
            recorded_at=KICKOFF - timedelta(days=1), source="the_odds_api",
        ),
        PlayerPropMarket(
            match_id=match.id, player_id=p2.id, bookmaker_id=bookmaker.id, market_type="player_goals",
            line_type="multi_plus", threshold=2.0, selection="yes", price_decimal=3.2,
            recorded_at=KICKOFF - timedelta(days=1), source="the_odds_api",
        ),
    ])
    db_session.commit()

    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=None, expected_bookmaker_name="SportsBet")
    assert diagnosis.disposals_available is True
    assert diagnosis.goals_available is True
    assert diagnosis.unique_player_count == 2


def test_goals_available_reported_even_when_disposal_market_absent(db_session):
    match, home, away = _seed(db_session)
    match.external_ids = {"the_odds_api": "evt1"}
    player = Player(sport_id=match.sport_id, display_name="Charlie Curnow", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="SportsBet")
    db_session.add_all([player, bookmaker])
    db_session.flush()
    db_session.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_goal_scorer_anytime",
        line_type="multi_plus", threshold=1.0, selection="yes", price_decimal=2.5,
        recorded_at=KICKOFF - timedelta(days=1), source="the_odds_api",
    ))
    db_session.commit()

    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=None)
    assert diagnosis.category == DIAG_DISPOSAL_MARKET_ABSENT
    assert diagnosis.disposals_available is False
    assert diagnosis.unique_player_count == 1


def test_market_flags_default_false_when_no_quotes_exist(db_session):
    match, home, away = _seed(db_session)
    match.external_ids = {"the_odds_api": "evt1"}
    db_session.commit()

    diagnosis = diagnose_match_market_coverage(db_session, match, provider_events=None)
    assert diagnosis.category == DIAG_EVENT_NO_PROPS
    assert diagnosis.disposals_available is False
    assert diagnosis.goals_available is False
    assert diagnosis.unique_player_count == 0
