from datetime import datetime, timezone

from sqlalchemy import select

from app.models import Match, MatchStatus, OddsQuote, Round, Season, Sport, Team
from app.player_modelling.team_odds_ingestion import ingest_team_odds
from app.providers.afl.the_odds_api import AFL_SPORT_KEY, StandardMatchOddsResult
from app.providers.player_prop_odds import QuotaStatus
from app.providers.types import ProviderEvent, TeamOddsQuote

NOW = datetime.now(timezone.utc)


def _seed_match(db, *, home_name="Collingwood", away_name="Carlton"):
    sport = Sport(code="AFL", name="Australian Football League")
    db.add(sport)
    db.flush()
    season = Season(sport_id=sport.id, year=2026)
    db.add(season)
    db.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name=home_name, short_name=home_name[:3].upper())
    away = Team(sport_id=sport.id, name=away_name, short_name=away_name[:3].upper())
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=NOW, status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.commit()
    return match, home, away


def _event(home="Collingwood", away="Carlton", commence=NOW):
    return ProviderEvent(provider="the_odds_api", event_id="evt1", sport_key=AFL_SPORT_KEY, home_team=home, away_team=away, commence_time=commence)


def _quote(**overrides):
    base = dict(
        provider="the_odds_api", event_id="evt1", sport_code="AFL",
        bookmaker_key="sportsbet", bookmaker_title="Sportsbet", bookmaker_region="au",
        market_key="h2h", selection="Collingwood", price_decimal=1.85,
        bookmaker_last_update=NOW, fetched_at=NOW, line_value=None,
    )
    base.update(overrides)
    return TeamOddsQuote(**base)


def _result(events, quotes):
    return StandardMatchOddsResult(events=events, quotes=quotes, quota=QuotaStatus(), markets_requested=["h2h"], markets_returned=["h2h"])


def test_h2h_quote_ingested_with_canonical_team_name(db_session):
    match, home, away = _seed_match(db_session)
    result = _result([_event()], [_quote(market_key="h2h", selection="Collingwood", price_decimal=1.85)])

    report = ingest_team_odds(db_session, result)
    assert report.quotes_created == 1
    row = db_session.scalar(select(OddsQuote).where(OddsQuote.match_id == match.id))
    assert row.market_type == "h2h"
    assert row.selection == "Collingwood"
    assert row.source == "the_odds_api"


def test_spreads_mapped_to_line_market_type(db_session):
    match, home, away = _seed_match(db_session)
    result = _result([_event()], [_quote(market_key="spreads", selection="Collingwood", price_decimal=1.9, line_value=-12.5)])

    ingest_team_odds(db_session, result)
    row = db_session.scalar(select(OddsQuote).where(OddsQuote.match_id == match.id))
    assert row.market_type == "line"
    assert row.line_value == -12.5


def test_totals_mapped_to_total_with_lowercase_selection(db_session):
    match, home, away = _seed_match(db_session)
    result = _result([_event()], [_quote(market_key="totals", selection="Over", price_decimal=1.9, line_value=165.5)])

    ingest_team_odds(db_session, result)
    row = db_session.scalar(select(OddsQuote).where(OddsQuote.match_id == match.id))
    assert row.market_type == "total"
    assert row.selection == "over"


def test_team_name_alias_resolved_to_canonical_name(db_session):
    match, home, away = _seed_match(db_session, home_name="Greater Western Sydney", away_name="Gold Coast")
    result = _result(
        [_event(home="GWS Giants", away="Gold Coast Suns")],
        [_quote(market_key="h2h", selection="GWS Giants", price_decimal=2.0)],
    )

    report = ingest_team_odds(db_session, result)
    assert report.matches_resolved == 1
    row = db_session.scalar(select(OddsQuote).where(OddsQuote.match_id == match.id))
    assert row.selection == "Greater Western Sydney"


def test_unresolved_team_name_is_reported_not_silently_dropped(db_session):
    _seed_match(db_session)
    result = _result([_event()], [_quote(market_key="h2h", selection="Some Unknown Team FC", price_decimal=2.0)])

    report = ingest_team_odds(db_session, result)
    assert report.quotes_created == 0
    assert any("Some Unknown Team FC" in s for s in report.unresolved_selections)


def test_unresolved_match_is_reported(db_session):
    result = _result([_event(home="Nonexistent Team", away="Also Nonexistent")], [_quote()])
    report = ingest_team_odds(db_session, result)
    assert report.matches_resolved == 0
    assert len(report.matches_unresolved) == 1


def test_idempotent_reingest_of_unchanged_price_does_not_duplicate(db_session):
    _seed_match(db_session)
    result = _result([_event()], [_quote(price_decimal=1.85)])

    first = ingest_team_odds(db_session, result)
    second = ingest_team_odds(db_session, result)
    assert first.quotes_created == 1
    assert second.quotes_created == 0
    assert second.quotes_unchanged == 1


def test_changed_price_creates_new_snapshot_row(db_session):
    _seed_match(db_session)
    ingest_team_odds(db_session, _result([_event()], [_quote(price_decimal=1.85)]))
    report = ingest_team_odds(db_session, _result([_event()], [_quote(price_decimal=1.95)]))
    assert report.quotes_created == 1

    rows = db_session.scalars(select(OddsQuote)).all()
    assert len(rows) == 2


def test_unsupported_market_key_is_skipped_and_reported(db_session):
    _seed_match(db_session)
    result = _result([_event()], [_quote(market_key="outrights", selection="Collingwood")])
    report = ingest_team_odds(db_session, result)
    assert report.quotes_created == 0
    assert "outrights" in report.unsupported_markets
