"""Tests for the Placed Bets tracker (app/player_modelling/placed_bets.py):
create/list/delete, and settlement for both player and team markets -
reusing the same primitives prop_settlement.py and
weekly_shortlist_snapshot_service.py already use, never duplicated math."""

from datetime import datetime, timedelta, timezone

from app.models import (
    Bookmaker,
    Match,
    MatchStatus,
    PlacedBet,
    Player,
    PlayerMatchStat,
    Round,
    STATUS_LOST,
    STATUS_PENDING,
    STATUS_VOID,
    STATUS_WON,
    Season,
    Sport,
    Team,
)
from app.player_modelling.placed_bets import (
    PlacedBetInput,
    create_placed_bet,
    delete_placed_bet,
    list_placed_bets,
    settle_placed_bets,
)

NOW = datetime.now(timezone.utc)


def _seed_match(db, status=MatchStatus.COMPLETED, home_score=100, away_score=80):
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
        scheduled_start=NOW - timedelta(days=1), status=status, home_score=home_score, away_score=away_score,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    db.commit()
    return match, home, away, player


def _player_bet_input(match, player, **overrides) -> PlacedBetInput:
    defaults = dict(
        match_id=match.id, opportunity_type="player", label="Nick Daicos 25+ Disposals", selection="over",
        market_type="player_disposals", bookmaker="SportsBet", odds_taken=1.9,
        model_probability=0.6, model_fair_odds=1.67, confidence_tier="higher_confidence",
        source_mode="high_probability", player_id=player.id, line_type="over_under", threshold=24.5,
    )
    defaults.update(overrides)
    return PlacedBetInput(**defaults)


def _team_bet_input(match, home, **overrides) -> PlacedBetInput:
    defaults = dict(
        match_id=match.id, opportunity_type="team", label="Collingwood to win", selection=home.name,
        market_type="h2h", bookmaker="TAB", odds_taken=1.7,
        model_probability=0.62, model_fair_odds=1.61, confidence_tier="higher_confidence",
        source_mode="best_opportunity",
    )
    defaults.update(overrides)
    return PlacedBetInput(**defaults)


def test_create_placed_bet_freezes_snapshot_and_starts_pending(db_session):
    match, home, away, player = _seed_match(db_session, status=MatchStatus.SCHEDULED)
    bet = create_placed_bet(db_session, _player_bet_input(match, player))

    assert bet.id is not None
    assert bet.status == STATUS_PENDING
    assert bet.model_probability == 0.6
    assert bet.settled_at is None
    assert bet.placed_at is not None  # defaulted, not left null


def test_list_placed_bets_filters_by_status(db_session):
    match, home, away, player = _seed_match(db_session, status=MatchStatus.SCHEDULED)
    create_placed_bet(db_session, _player_bet_input(match, player))
    create_placed_bet(db_session, _team_bet_input(match, home))

    assert len(list_placed_bets(db_session)) == 2
    assert len(list_placed_bets(db_session, status=STATUS_PENDING)) == 2
    assert len(list_placed_bets(db_session, status=STATUS_WON)) == 0


def test_delete_placed_bet(db_session):
    match, home, away, player = _seed_match(db_session, status=MatchStatus.SCHEDULED)
    bet = create_placed_bet(db_session, _player_bet_input(match, player))

    assert delete_placed_bet(db_session, bet.id) is True
    assert list_placed_bets(db_session) == []
    assert delete_placed_bet(db_session, bet.id) is False  # already gone


def test_settle_player_bet_won(db_session):
    match, home, away, player = _seed_match(db_session)
    db_session.add(PlayerMatchStat(player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30))
    db_session.commit()
    create_placed_bet(db_session, _player_bet_input(match, player, threshold=24.5))

    report = settle_placed_bets(db_session)

    assert report.bets_settled == 1
    assert report.bets_won == 1
    bet = db_session.query(PlacedBet).one()
    assert bet.status == STATUS_WON
    assert bet.actual_stat_value == 30.0
    assert bet.settled_at is not None


def test_settle_player_bet_lost(db_session):
    match, home, away, player = _seed_match(db_session)
    db_session.add(PlayerMatchStat(player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=20))
    db_session.commit()
    create_placed_bet(db_session, _player_bet_input(match, player, threshold=24.5))

    settle_placed_bets(db_session)

    assert db_session.query(PlacedBet).one().status == STATUS_LOST


def test_settle_player_bet_awaiting_stats_stays_pending(db_session):
    match, home, away, player = _seed_match(db_session)  # COMPLETED but no PlayerMatchStat at all yet
    create_placed_bet(db_session, _player_bet_input(match, player))

    report = settle_placed_bets(db_session)

    assert report.bets_settled == 0
    assert report.awaiting_data == 1
    assert db_session.query(PlacedBet).one().status == STATUS_PENDING


def test_settle_player_bet_genuine_dnp_voids(db_session):
    match, home, away, player = _seed_match(db_session)
    other = Player(sport_id=player.sport_id, display_name="Other Player", source="afltables", source_player_id="p2", current_team_id=home.id)
    db_session.add(other)
    db_session.flush()
    db_session.add(PlayerMatchStat(player_id=other.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=15))
    db_session.commit()
    create_placed_bet(db_session, _player_bet_input(match, player))  # player never got a stat row - DNP

    report = settle_placed_bets(db_session)

    assert report.bets_voided == 1
    assert db_session.query(PlacedBet).one().status == STATUS_VOID


def test_settle_team_bet_reuses_compute_team_market_result(db_session):
    match, home, away, player = _seed_match(db_session, home_score=100, away_score=80)
    create_placed_bet(db_session, _team_bet_input(match, home))

    report = settle_placed_bets(db_session)

    assert report.bets_won == 1
    bet = db_session.query(PlacedBet).one()
    assert bet.status == STATUS_WON
    assert bet.actual_stat_value == 20.0  # home margin


def test_settle_team_bet_not_completed_awaits_data(db_session):
    match, home, away, player = _seed_match(db_session, status=MatchStatus.SCHEDULED)
    create_placed_bet(db_session, _team_bet_input(match, home))

    report = settle_placed_bets(db_session)

    assert report.awaiting_data == 1
    assert db_session.query(PlacedBet).one().status == STATUS_PENDING


def test_settlement_is_idempotent(db_session):
    match, home, away, player = _seed_match(db_session, home_score=100, away_score=80)
    create_placed_bet(db_session, _team_bet_input(match, home))

    settle_placed_bets(db_session)
    first_settled_at = db_session.query(PlacedBet).one().settled_at

    match.home_score = 50  # would flip the result if re-settled
    db_session.commit()
    second_report = settle_placed_bets(db_session)

    assert second_report.bets_settled == 0
    bet = db_session.query(PlacedBet).one()
    assert bet.status == STATUS_WON  # unchanged
    assert bet.settled_at == first_settled_at
