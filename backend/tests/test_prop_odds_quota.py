from datetime import datetime, timedelta, timezone

from app.models import Bookmaker, Match, MatchStatus, Player, PlayerPropMarket, Round, Season, Sport, Team
from app.player_modelling.prop_odds_quota import DEFAULT_MIN_REFRESH_INTERVAL, event_needs_refresh, recommended_refresh_interval


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


# --- Match-time-aware refresh policy (live-operations stage, Section 4) ----


def test_recommended_interval_very_infrequent_when_far_out():
    assert recommended_refresh_interval(100.0) == timedelta(hours=24)


def test_recommended_interval_occasional_24_to_72h():
    assert recommended_refresh_interval(48.0) == timedelta(hours=6)


def test_recommended_interval_more_frequent_6_to_24h():
    assert recommended_refresh_interval(12.0) == timedelta(hours=2)


def test_recommended_interval_more_frequent_again_1_to_6h():
    assert recommended_refresh_interval(3.0) == timedelta(hours=1)


def test_recommended_interval_highest_frequency_under_1h():
    assert recommended_refresh_interval(0.5) == DEFAULT_MIN_REFRESH_INTERVAL


def test_recommended_interval_never_below_the_floor_even_mid_match():
    # A negative value (match already started) must not be looser OR
    # tighter than the pre-match floor - it falls into the same <1h band.
    assert recommended_refresh_interval(-0.2) == DEFAULT_MIN_REFRESH_INTERVAL


def test_recommended_interval_band_boundaries_are_exact():
    # Each band's own min_hours is inclusive, so exactly 72.0/24.0/6.0/1.0
    # hours out still belongs to the band starting AT that boundary (the
    # "more than 72 hours" band means >=72, matching Section 4's own
    # wording), not the next tighter one.
    assert recommended_refresh_interval(72.0) == timedelta(hours=24)
    assert recommended_refresh_interval(24.0) == timedelta(hours=6)
    assert recommended_refresh_interval(6.0) == timedelta(hours=2)
    assert recommended_refresh_interval(1.0) == timedelta(hours=1)


def test_recommended_interval_is_configurable_via_custom_bands():
    from app.player_modelling.prop_odds_quota import RefreshBand

    custom = [RefreshBand(0.0, float("inf"), timedelta(minutes=5))]
    assert recommended_refresh_interval(200.0, bands=custom) == timedelta(minutes=5)
