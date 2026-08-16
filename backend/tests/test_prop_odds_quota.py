from datetime import datetime, timedelta, timezone

from app.models import Bookmaker, Match, MatchStatus, Player, PlayerPropMarket, Round, Season, Sport, Team
from app.player_modelling.prop_odds_quota import event_needs_refresh


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
        scheduled_start=datetime(2026, 8, 20, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    bookmaker = Bookmaker(name="Sportsbet")
    db.add_all([match, player, bookmaker])
    db.commit()
    return match, player, bookmaker


def _add_quote(db, match, player, bookmaker, recorded_at, source="the_odds_api"):
    db.add(
        PlayerPropMarket(
            match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
            line_type="over_under", threshold=24.5, selection="over", price_decimal=1.9, recorded_at=recorded_at, source=source,
        )
    )
    db.commit()


def test_needs_refresh_when_no_prior_quote_exists(db_session):
    match, player, bookmaker = _seed(db_session)
    assert event_needs_refresh(db_session, match.id, "the_odds_api") is True


def test_does_not_need_refresh_when_recently_fetched(db_session):
    match, player, bookmaker = _seed(db_session)
    _add_quote(db_session, match, player, bookmaker, datetime.now(timezone.utc) - timedelta(minutes=5))
    assert event_needs_refresh(db_session, match.id, "the_odds_api") is False


def test_needs_refresh_when_last_quote_older_than_interval(db_session):
    match, player, bookmaker = _seed(db_session)
    _add_quote(db_session, match, player, bookmaker, datetime.now(timezone.utc) - timedelta(hours=1))
    assert event_needs_refresh(db_session, match.id, "the_odds_api", min_interval=timedelta(minutes=30)) is True


def test_custom_min_interval_respected(db_session):
    match, player, bookmaker = _seed(db_session)
    _add_quote(db_session, match, player, bookmaker, datetime.now(timezone.utc) - timedelta(minutes=5))
    assert event_needs_refresh(db_session, match.id, "the_odds_api", min_interval=timedelta(minutes=1)) is True


def test_only_considers_quotes_from_the_same_provider(db_session):
    match, player, bookmaker = _seed(db_session)
    _add_quote(db_session, match, player, bookmaker, datetime.now(timezone.utc) - timedelta(minutes=1), source="manual")
    # a fresh MANUAL quote must not suppress an automated refresh - a
    # different provider's freshness is a separate concern.
    assert event_needs_refresh(db_session, match.id, "the_odds_api") is True
