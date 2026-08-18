"""Tests for consensus bookmaker probability + outlier detection (Weekly
Bet Review stage, Sections 9-10)."""

from datetime import datetime, timedelta, timezone

from app.models import Bookmaker, Match, MatchStatus, OddsQuote, Round, Season, Sport, Team
from app.player_modelling.consensus_and_outliers import detect_outlier_bookmaker, team_consensus

NOW = datetime.now(timezone.utc)


def _book(name, price, eligibility="included"):
    return {"bookmaker_name": name, "price_decimal": price, "eligibility": eligibility}


def test_no_outlier_with_fewer_than_three_eligible_books():
    result = detect_outlier_bookmaker([_book("A", 5.0), _book("B", 2.0)])
    assert result is None


def test_outlier_detected_when_best_price_far_above_rest():
    result = detect_outlier_bookmaker([_book("A", 5.0), _book("B", 2.0), _book("C", 1.9), _book("D", 1.95)])
    assert result.is_outlier is True
    assert "outlier" in result.message.lower()


def test_no_outlier_when_prices_are_tight():
    result = detect_outlier_bookmaker([_book("A", 2.0), _book("B", 1.95), _book("C", 1.9)])
    assert result.is_outlier is False
    assert result.message is None


def test_exchange_bookmaker_excluded_from_outlier_check():
    result = detect_outlier_bookmaker([
        _book("Betfair", 34.0, eligibility="informational_only"),
        _book("A", 2.0), _book("B", 1.95), _book("C", 1.9),
    ])
    assert result.is_outlier is False  # Betfair never counted


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


def _add_h2h_quote(db, match, bookmaker_name, selection, price):
    bookmaker = db.query(Bookmaker).filter_by(name=bookmaker_name).first()
    if bookmaker is None:
        bookmaker = Bookmaker(name=bookmaker_name)
        db.add(bookmaker)
        db.flush()
    db.add(OddsQuote(match_id=match.id, bookmaker_id=bookmaker.id, market_type="h2h", selection=selection, line_value=None, price_decimal=price, recorded_at=NOW, source="manual", is_closing_line=False))
    db.commit()


def test_team_consensus_devigs_when_both_sides_quoted_by_same_book(db_session):
    match, home, away = _seed_match(db_session)
    _add_h2h_quote(db_session, match, "TAB", home.name, 1.90)
    _add_h2h_quote(db_session, match, "TAB", away.name, 1.95)

    opportunity = {
        "opportunity_type": "team", "match_id": match.id, "market_type": "h2h", "selection": home.name, "line_value": None,
        "bookmakers": [{"bookmaker_name": "TAB", "price_decimal": 1.90, "eligibility": "included"}],
    }
    result = team_consensus(db_session, opportunity)
    assert result is not None
    assert result.n_devigged == 1
    assert result.per_bookmaker[0].overround_removed is True
    # devigged probability should be less than raw implied (which has margin baked in)
    assert result.per_bookmaker[0].probability < 1 / 1.90


def test_team_consensus_uses_raw_implied_when_only_one_side_quoted(db_session):
    match, home, away = _seed_match(db_session)
    _add_h2h_quote(db_session, match, "TAB", home.name, 1.90)

    opportunity = {
        "opportunity_type": "team", "match_id": match.id, "market_type": "h2h", "selection": home.name, "line_value": None,
        "bookmakers": [{"bookmaker_name": "TAB", "price_decimal": 1.90, "eligibility": "included"}],
    }
    result = team_consensus(db_session, opportunity)
    assert result.n_devigged == 0
    assert result.per_bookmaker[0].overround_removed is False
    assert round(result.per_bookmaker[0].probability, 4) == round(1 / 1.90, 4)


def test_team_consensus_line_market_never_devigged(db_session):
    match, home, away = _seed_match(db_session)
    opportunity = {
        "opportunity_type": "team", "match_id": match.id, "market_type": "line", "selection": home.name, "line_value": -11.5,
        "bookmakers": [{"bookmaker_name": "TAB", "price_decimal": 1.90, "eligibility": "included"}],
    }
    result = team_consensus(db_session, opportunity)
    assert result.n_devigged == 0
    assert "not implemented" in result.methodology.lower() or "raw implied" in result.methodology.lower()


def test_team_consensus_none_with_no_eligible_bookmakers(db_session):
    match, home, away = _seed_match(db_session)
    opportunity = {"opportunity_type": "team", "match_id": match.id, "market_type": "h2h", "selection": home.name, "line_value": None, "bookmakers": []}
    assert team_consensus(db_session, opportunity) is None
