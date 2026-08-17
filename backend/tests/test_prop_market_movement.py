"""Tests for market movement + closing-price derivation (Sections 5-6, 23
of the market-logging stage brief): first/latest/highest/lowest odds per
player/market/bookmaker, the "latest observed pre-match price" closing
definition, and that a post-match-start quote is never treated as closing."""

from datetime import datetime, timedelta, timezone

from app.models import Bookmaker, Match, MatchStatus, Player, PropMarketObservation, Round, Season, Sport, Team
from app.player_modelling.prop_market_movement import closing_quote_for, compute_market_movement

KICKOFF = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _same_instant(a: datetime, b: datetime) -> bool:
    """SQLite round-trips DateTime(timezone=True) values without tzinfo, so
    a value read back after commit compares unequal to the naive-vs-aware
    original unless both sides are normalized first."""
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return a == b


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
    return match, home, away, player, bookmaker


def _obs(match, player, bookmaker, *, odds, observed_at, threshold=29.5, diff=0.02):
    return PropMarketObservation(
        quote_id=1, match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id,
        market_type="player_disposals", line_type="over_under", threshold=threshold, source="the_odds_api",
        offered_odds=odds, observed_at=observed_at, raw_implied_probability=1 / odds,
        devigged_probability=None, overround_removed=False,
        model_probability=0.55, model_fair_odds=1.82, predicted_mean=28.0,
        model_name="disposals_nb", model_version="v1", data_cutoff=observed_at,
        confidence_tier="moderate_confidence", selection_status_at_observation="placeholder",
        is_confirmed_at_observation=False, difference_pp=diff, expected_value=0.045,
    )


def test_movement_tracks_first_latest_highest_lowest(db_session):
    match, home, away, player, bookmaker = _seed(db_session)
    t1 = KICKOFF - timedelta(days=2)
    t2 = KICKOFF - timedelta(days=1)
    t3 = KICKOFF - timedelta(hours=6)
    db_session.add_all([
        _obs(match, player, bookmaker, odds=1.90, observed_at=t1, diff=0.02),
        _obs(match, player, bookmaker, odds=2.10, observed_at=t2, diff=0.05),
        _obs(match, player, bookmaker, odds=1.75, observed_at=t3, diff=-0.01),
    ])
    db_session.commit()

    movements = compute_market_movement(db_session, match_id=match.id)

    assert len(movements) == 1
    m = movements[0]
    assert m.player_id == player.id
    assert m.player_name == "Nick Daicos"
    assert m.first_odds == 1.90
    assert m.latest_odds == 1.75
    assert m.highest_odds == 2.10
    assert m.lowest_odds == 1.75
    assert m.first_difference_pp == 0.02
    assert m.latest_difference_pp == -0.01
    assert m.n_observations == 3


def test_movement_grouped_separately_per_bookmaker(db_session):
    match, home, away, player, bookmaker = _seed(db_session)
    other_bookmaker = Bookmaker(name="TAB")
    db_session.add(other_bookmaker)
    db_session.flush()
    t1 = KICKOFF - timedelta(days=1)
    db_session.add_all([
        _obs(match, player, bookmaker, odds=1.90, observed_at=t1),
        _obs(match, player, other_bookmaker, odds=2.00, observed_at=t1),
    ])
    db_session.commit()

    movements = compute_market_movement(db_session, match_id=match.id)

    assert len(movements) == 2
    assert {m.bookmaker_name for m in movements} == {"SportsBet", "TAB"}


def test_movement_grouped_separately_per_threshold(db_session):
    match, home, away, player, bookmaker = _seed(db_session)
    t1 = KICKOFF - timedelta(days=1)
    db_session.add_all([
        _obs(match, player, bookmaker, odds=1.90, observed_at=t1, threshold=27.5),
        _obs(match, player, bookmaker, odds=2.50, observed_at=t1, threshold=31.5),
    ])
    db_session.commit()

    movements = compute_market_movement(db_session, match_id=match.id)

    assert len(movements) == 2
    assert {m.threshold for m in movements} == {27.5, 31.5}


def test_closing_quote_is_latest_pre_match_quote(db_session):
    match, home, away, player, bookmaker = _seed(db_session)
    t1 = KICKOFF - timedelta(days=2)
    t2 = KICKOFF - timedelta(hours=3)
    db_session.add_all([
        _obs(match, player, bookmaker, odds=1.90, observed_at=t1),
        _obs(match, player, bookmaker, odds=1.80, observed_at=t2),
    ])
    db_session.commit()

    closing = closing_quote_for(
        db_session, match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id,
        market_type="player_disposals", line_type="over_under", threshold=29.5,
    )

    assert closing is not None
    assert closing.offered_odds == 1.80
    assert _same_instant(closing.observed_at, t2)


def test_post_match_start_quote_never_considered_closing(db_session):
    match, home, away, player, bookmaker = _seed(db_session)
    t_before = KICKOFF - timedelta(hours=1)
    t_after = KICKOFF + timedelta(minutes=30)  # e.g. a late/live-market update
    db_session.add_all([
        _obs(match, player, bookmaker, odds=1.85, observed_at=t_before),
        _obs(match, player, bookmaker, odds=1.50, observed_at=t_after),
    ])
    db_session.commit()

    closing = closing_quote_for(
        db_session, match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id,
        market_type="player_disposals", line_type="over_under", threshold=29.5,
    )

    assert closing is not None
    assert closing.offered_odds == 1.85  # NOT the 1.50 post-start row
    assert _same_instant(closing.observed_at, t_before)


def test_closing_quote_none_when_no_pre_match_quote_exists(db_session):
    match, home, away, player, bookmaker = _seed(db_session)
    db_session.add(_obs(match, player, bookmaker, odds=1.50, observed_at=KICKOFF + timedelta(minutes=10)))
    db_session.commit()

    closing = closing_quote_for(
        db_session, match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id,
        market_type="player_disposals", line_type="over_under", threshold=29.5,
    )

    assert closing is None
