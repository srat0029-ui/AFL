from datetime import datetime, timezone

from app.models import Bookmaker, Match, MatchStatus, OddsQuote, Round, Season, Sport, Team
from app.providers.afl.manual_odds import ManualOddsProvider


def _seed_match_with_odds(db_session) -> Match:
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()

    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()

    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    away = Team(sport_id=sport.id, name="Richmond", short_name="RIC")
    db_session.add_all([round_, home, away])
    db_session.flush()

    match = Match(
        sport_id=sport.id,
        season_id=season.id,
        round_id=round_.id,
        home_team_id=home.id,
        away_team_id=away.id,
        scheduled_start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        status=MatchStatus.SCHEDULED,
    )
    db_session.add(match)
    db_session.flush()

    bookmaker = Bookmaker(name="Sportsbet")
    db_session.add(bookmaker)
    db_session.flush()

    quote = OddsQuote(
        match_id=match.id,
        bookmaker_id=bookmaker.id,
        market_type="h2h",
        selection="Carlton",
        price_decimal=1.85,
        recorded_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        source="manual",
        is_closing_line=False,
    )
    db_session.add(quote)
    db_session.commit()
    return match


def test_get_odds_returns_dto_matching_stored_quote(db_session):
    match = _seed_match_with_odds(db_session)
    provider = ManualOddsProvider(db_session)

    quotes = provider.get_odds("AFL", str(match.id))

    assert len(quotes) == 1
    q = quotes[0]
    assert q.match_external_id == str(match.id)
    assert q.bookmaker == "Sportsbet"
    assert q.market_type == "h2h"
    assert q.selection == "Carlton"
    assert q.price_decimal == 1.85
    assert q.source == "manual"
    assert q.is_closing_line is False


def test_get_odds_unknown_match_returns_empty(db_session):
    provider = ManualOddsProvider(db_session)
    assert provider.get_odds("AFL", "999999") == []


def test_get_odds_wrong_sport_returns_empty(db_session):
    match = _seed_match_with_odds(db_session)
    provider = ManualOddsProvider(db_session)

    assert provider.get_odds("NRL", str(match.id)) == []


def test_get_odds_match_with_no_quotes_returns_empty(db_session):
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=2026)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Geelong", short_name="GEE")
    away = Team(sport_id=sport.id, name="Sydney", short_name="SYD")
    db_session.add_all([round_, home, away])
    db_session.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id,
        home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(2026, 8, 21, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db_session.add(match)
    db_session.commit()

    provider = ManualOddsProvider(db_session)
    assert provider.get_odds("AFL", str(match.id)) == []
