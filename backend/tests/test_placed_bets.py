"""Tests for the Placed Bets tracker (app/player_modelling/placed_bets.py):
create/list/delete, and settlement for both player and team markets -
reusing the same primitives prop_settlement.py and
weekly_shortlist_snapshot_service.py already use, never duplicated math."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    Bookmaker,
    Match,
    MatchStatus,
    PlacedBet,
    Player,
    PlayerMatchStat,
    PlayerPropMarket,
    Round,
    STATUS_LOST,
    STATUS_PENDING,
    STATUS_PUSH,
    STATUS_VOID,
    STATUS_WON,
    Season,
    Sport,
    Team,
)
from app.player_modelling.placed_bets import (
    PlacedBetInput,
    compute_multi_group_status,
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


# --- Results Ingestion + Settlement Reliability Audit additions --------


def test_settle_player_bet_incomplete_match_stays_pending(db_session):
    match, home, away, player = _seed_match(db_session, status=MatchStatus.SCHEDULED)
    create_placed_bet(db_session, _player_bet_input(match, player))

    report = settle_placed_bets(db_session)

    assert report.bets_settled == 0
    assert report.matches_awaiting_player_stats == 1
    assert db_session.query(PlacedBet).one().status == STATUS_PENDING


def test_settle_completed_match_missing_player_stats_stays_pending(db_session):
    match, home, away, player = _seed_match(db_session)  # COMPLETED, but no PlayerMatchStat ingested yet
    create_placed_bet(db_session, _player_bet_input(match, player))

    report = settle_placed_bets(db_session)

    assert report.bets_settled == 0
    assert report.matches_awaiting_player_stats == 1
    assert db_session.query(PlacedBet).one().status == STATUS_PENDING


def _seed_prop_market_row(db, match, player, bookmaker_name, *, line_type, threshold, price, recorded_at):
    bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == bookmaker_name))
    if bookmaker is None:
        bookmaker = Bookmaker(name=bookmaker_name)
        db.add(bookmaker)
        db.flush()
    db.add(PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_goals",
        line_type=line_type, threshold=threshold, price_decimal=price, recorded_at=recorded_at, source="the_odds_api",
    ))
    db.commit()
    return bookmaker


def test_legacy_bet_missing_threshold_is_repaired_from_market_history_and_settles(db_session):
    """Reproduces the exact Elliot Yeo / Dylan Moore bug: a PlacedBet frozen
    with threshold=None, line_type=None (a data-entry gap, not intentional),
    silently stuck pending forever pre-fix. Settlement should recover the
    threshold/line_type from the PlayerPropMarket snapshot the bet's own
    odds_taken matches, then settle normally - never guessing, never
    touching already-frozen fields."""
    match, home, away, player = _seed_match(db_session)
    db_session.add(PlayerMatchStat(player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, goals=0))
    db_session.commit()
    placed_at = NOW - timedelta(days=2)
    _seed_prop_market_row(
        db_session, match, player, "PointsBet (AU)",
        line_type="over_under", threshold=0.5, price=2.2, recorded_at=placed_at - timedelta(hours=1),
    )
    bet = create_placed_bet(db_session, _player_bet_input(
        match, player, label="Elliot Yeo 0.5+ Goals", market_type="player_goals", bookmaker="PointsBet (AU)",
        odds_taken=2.2, line_type=None, threshold=None, placed_at=placed_at,
    ))

    report = settle_placed_bets(db_session)

    assert report.legs_repaired == 1
    assert report.bets_settled == 1
    refreshed = db_session.get(PlacedBet, bet.id)
    assert refreshed.line_type == "over_under"
    assert refreshed.threshold == 0.5
    assert refreshed.status == STATUS_LOST  # 0 goals doesn't clear 0.5


def test_legacy_bet_ambiguous_market_history_is_a_settlement_failure_not_a_guess(db_session):
    match, home, away, player = _seed_match(db_session)
    db_session.add(PlayerMatchStat(player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, goals=2))
    db_session.commit()
    placed_at = NOW - timedelta(days=2)
    at = placed_at - timedelta(hours=1)
    # Two different lines recorded at the exact same moment for the same
    # price - genuinely ambiguous, must not be guessed.
    _seed_prop_market_row(db_session, match, player, "PointsBet (AU)", line_type="over_under", threshold=0.5, price=2.2, recorded_at=at)
    _seed_prop_market_row(db_session, match, player, "PointsBet (AU)", line_type="multi_plus", threshold=1.0, price=2.2, recorded_at=at)
    create_placed_bet(db_session, _player_bet_input(
        match, player, bookmaker="PointsBet (AU)", odds_taken=2.2, line_type=None, threshold=None, placed_at=placed_at,
    ))

    report = settle_placed_bets(db_session)

    assert report.legs_repaired == 0
    assert report.settlement_failures == 1
    assert report.bets_settled == 0
    assert db_session.query(PlacedBet).one().status == STATUS_PENDING


def test_multi_group_status_all_legs_won_is_won():
    legs = [PlacedBet(status=STATUS_WON), PlacedBet(status=STATUS_WON)]
    assert compute_multi_group_status(legs) == STATUS_WON


def test_multi_group_status_any_leg_lost_is_lost():
    legs = [PlacedBet(status=STATUS_WON), PlacedBet(status=STATUS_LOST), PlacedBet(status=STATUS_PENDING)]
    assert compute_multi_group_status(legs) == STATUS_LOST


def test_multi_group_status_unresolved_leg_stays_pending():
    legs = [PlacedBet(status=STATUS_WON), PlacedBet(status=STATUS_PENDING)]
    assert compute_multi_group_status(legs) == STATUS_PENDING


def test_multi_group_status_void_leg_removed_from_consideration():
    legs = [PlacedBet(status=STATUS_WON), PlacedBet(status=STATUS_VOID)]
    assert compute_multi_group_status(legs) == STATUS_WON


def test_multi_group_status_all_void_is_void():
    legs = [PlacedBet(status=STATUS_VOID), PlacedBet(status=STATUS_PUSH)]
    assert compute_multi_group_status(legs) == STATUS_VOID


def test_settle_placed_bets_reports_winning_multi(db_session):
    match, home, away, player = _seed_match(db_session)
    other = Player(sport_id=player.sport_id, display_name="Other Player", source="afltables", source_player_id="p2", current_team_id=home.id)
    db_session.add(other)
    db_session.flush()
    db_session.add(PlayerMatchStat(player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30))
    db_session.add(PlayerMatchStat(player_id=other.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=28))
    db_session.commit()
    group_id = "multi-1"
    create_placed_bet(db_session, _player_bet_input(match, player, threshold=24.5, multi_group_id=group_id, multi_tier="balanced"))
    create_placed_bet(db_session, _player_bet_input(match, other, label="Other Player 22+ Disposals", threshold=21.5, multi_group_id=group_id, multi_tier="balanced"))

    report = settle_placed_bets(db_session)

    assert report.multis_settled == 1
    assert report.multis_won == 1
    assert all(b.status == STATUS_WON for b in db_session.query(PlacedBet).all())


def test_settle_placed_bets_reports_losing_multi(db_session):
    match, home, away, player = _seed_match(db_session)
    other = Player(sport_id=player.sport_id, display_name="Other Player", source="afltables", source_player_id="p2", current_team_id=home.id)
    db_session.add(other)
    db_session.flush()
    db_session.add(PlayerMatchStat(player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30))
    db_session.add(PlayerMatchStat(player_id=other.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=10))
    db_session.commit()
    group_id = "multi-2"
    create_placed_bet(db_session, _player_bet_input(match, player, threshold=24.5, multi_group_id=group_id, multi_tier="balanced"))
    create_placed_bet(db_session, _player_bet_input(match, other, label="Other Player 22+ Disposals", threshold=21.5, multi_group_id=group_id, multi_tier="balanced"))

    report = settle_placed_bets(db_session)

    assert report.multis_settled == 1
    assert report.multis_lost == 1
    statuses = {b.status for b in db_session.query(PlacedBet).all()}
    assert STATUS_LOST in statuses  # the losing leg killed the multi even though the other leg won


def test_duplicated_leg_across_two_multis_settles_independently_but_consistently(db_session):
    """Section 4: the same player/threshold reused across two multi tiers
    is two separate PlacedBet rows - each settles independently against the
    same PlayerMatchStat, so they always agree without special-casing."""
    match, home, away, player = _seed_match(db_session)
    db_session.add(PlayerMatchStat(player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30))
    db_session.commit()
    create_placed_bet(db_session, _player_bet_input(match, player, threshold=24.5, multi_group_id="multi-a", multi_tier="balanced"))
    create_placed_bet(db_session, _player_bet_input(match, player, threshold=24.5, multi_group_id="multi-b", multi_tier="conservative"))

    report = settle_placed_bets(db_session)

    assert report.multis_settled == 2
    assert report.multis_won == 2
    assert {b.status for b in db_session.query(PlacedBet).all()} == {STATUS_WON}


def test_rerun_settles_only_newly_eligible_legs_and_never_rewrites_settled_ones(db_session):
    match, home, away, player = _seed_match(db_session)
    other = Player(sport_id=player.sport_id, display_name="Other Player", source="afltables", source_player_id="p2", current_team_id=home.id)
    db_session.add(other)
    db_session.flush()
    db_session.add(PlayerMatchStat(player_id=player.id, match_id=match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30))
    db_session.commit()
    settled_bet = create_placed_bet(db_session, _player_bet_input(match, player, threshold=24.5))

    first_report = settle_placed_bets(db_session)
    assert first_report.bets_won == 1
    first_settled_at = db_session.get(PlacedBet, settled_bet.id).settled_at

    # A new pending leg shows up for a player who never got a stat row for
    # this (already-stats-ingested) match - a genuine DNP, not "awaiting
    # data", since match-level stats clearly exist already.
    new_bet = create_placed_bet(db_session, _player_bet_input(match, other, label="Other Player 22+ Disposals", threshold=21.5))
    second_report = settle_placed_bets(db_session)

    assert second_report.legs_checked == 1  # only the still-pending one is looked at
    assert second_report.bets_voided == 1
    assert db_session.get(PlacedBet, settled_bet.id).status == STATUS_WON
    assert db_session.get(PlacedBet, settled_bet.id).settled_at == first_settled_at  # untouched
    assert db_session.get(PlacedBet, new_bet.id).status == STATUS_VOID
