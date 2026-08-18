"""Tests for market movement interpretation (Weekly Bet Review stage,
Section 8) — classification is judged in implied-probability space, and
DB-backed team/player lookups read the full historical snapshot list."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Bookmaker, Match, MatchStatus, OddsQuote, Round, Season, Sport, Team
from app.player_modelling.market_movement import (
    AWAY_FROM_MODEL,
    TOWARD_MODEL,
    UNCHANGED,
    _classify_direction,
    team_market_movement,
)

NOW = datetime.now(timezone.utc)


def test_unchanged_when_price_never_moved():
    direction, _ = _classify_direction(2.0, 2.0, 1.8)
    assert direction == UNCHANGED


def test_toward_model_when_price_shortens_toward_fair():
    # Fair is 1.58 (like Port Adelaide's real case): opened 2.10, now 1.91 - closer to fair.
    direction, desc = _classify_direction(2.10, 1.91, 1.58)
    assert direction == TOWARD_MODEL
    assert "toward" in desc


def test_away_from_model_when_price_drifts_further_from_fair():
    # Real Collingwood case: fair 2.16, opened 3.70, now 4.10 - further away.
    direction, desc = _classify_direction(3.70, 4.10, 2.16)
    assert direction == AWAY_FROM_MODEL
    assert "away" in desc


def test_toward_model_judged_in_probability_space_not_raw_price_gap():
    # A long-priced selection: raw price gap can be large even when the
    # probability gap shrinks - this must use implied probability, not
    # raw decimal-odds distance.
    direction, _ = _classify_direction(15.0, 10.0, 5.0)
    assert direction == TOWARD_MODEL


def _seed_match(db):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    db.add(round_)
    db.flush()
    home = Team(sport_id=sport.id, name="Collingwood", short_name="COL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db.add_all([home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW + timedelta(days=1), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


def test_team_market_movement_uses_earliest_and_latest_snapshot(db_session):
    match, home, away = _seed_match(db_session)
    tab = Bookmaker(name="TAB")
    sportsbet = Bookmaker(name="SportsBet")
    db_session.add_all([tab, sportsbet])
    db_session.flush()
    db_session.add(OddsQuote(match_id=match.id, bookmaker_id=tab.id, market_type="h2h", selection=home.name, line_value=None, price_decimal=3.70, recorded_at=NOW - timedelta(hours=20), source="manual", is_closing_line=False))
    db_session.add(OddsQuote(match_id=match.id, bookmaker_id=sportsbet.id, market_type="h2h", selection=home.name, line_value=None, price_decimal=4.10, recorded_at=NOW, source="manual", is_closing_line=False))
    db_session.commit()

    movement = team_market_movement(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None, model_fair_odds=2.16, best_current_price=4.30)
    assert movement is not None
    assert movement.first_price == 3.70
    assert movement.latest_price == 4.10
    assert movement.direction == AWAY_FROM_MODEL


def test_team_market_movement_none_when_no_quotes(db_session):
    match, home, away = _seed_match(db_session)
    movement = team_market_movement(db_session, match_id=match.id, market_type="h2h", selection=home.name, line_value=None, model_fair_odds=2.0, best_current_price=2.0)
    assert movement is None
