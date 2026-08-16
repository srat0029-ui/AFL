"""End-to-end orchestration tests for run_prop_odds_refresh (Sections 6-10
of the automated-odds stage) using a FAKE PlayerPropOddsProvider (no real
network, no mocked HTTP even — the fake implements the interface directly,
matching this codebase's stated seam: consumers only depend on
PlayerPropOddsProvider, never a concrete provider class)."""

from datetime import datetime, timedelta, timezone

from app.models import Match, MatchStatus, Player, PlayerPropMarket, Round, Season, Sport, Team
from app.player_modelling.prop_odds_ingestion import run_prop_odds_refresh
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.providers.player_prop_odds import PlayerPropOddsProvider, PlayerPropOddsResult, QuotaStatus
from app.providers.types import PlayerPropQuote, ProviderEvent

MARKET_KEYS = ["player_disposals", "player_goal_scorer_anytime"]


class FakeProvider(PlayerPropOddsProvider):
    def __init__(self, events, quotes_by_event, available=True):
        self._events = events
        self._quotes_by_event = quotes_by_event
        self._available = available
        self.fetch_calls = []

    @property
    def is_available(self) -> bool:
        return self._available

    def list_events(self, sport_code):
        return self._events

    def get_player_prop_quotes(self, sport_code, event, market_keys):
        self.fetch_calls.append(event.event_id)
        quotes = self._quotes_by_event.get(event.event_id, [])
        return PlayerPropOddsResult(quotes=quotes, quota=QuotaStatus(requests_used=1, requests_remaining=499), markets_returned=market_keys)


def _seed_match(db):
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
        scheduled_start=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    db.flush()
    player = Player(sport_id=sport.id, display_name="Nick Daicos", source="afltables", source_player_id="p1", current_team_id=home.id)
    db.add(player)
    db.commit()
    return match, home, away, player


def _event(match):
    return ProviderEvent(
        provider="the_odds_api", event_id="evt1", sport_key="aussierules_afl",
        home_team="Collingwood", away_team="Carlton", commence_time=match.scheduled_start,
    )


def _quote(player_name="Nick Daicos", market_key="player_disposals", selection="Over", threshold=29.5, price=1.9, last_update=None, event_id="evt1"):
    now = datetime.now(timezone.utc)
    return PlayerPropQuote(
        provider="the_odds_api", event_id=event_id, sport_code="AFL", bookmaker_key="sportsbet", bookmaker_title="Sportsbet",
        bookmaker_region="au", market_key=market_key, player_name=player_name, selection=selection, price_decimal=price,
        bookmaker_last_update=last_update or now, fetched_at=now, threshold=threshold,
    )


def test_provider_unavailable_returns_gracefully(db_session):
    match, *_ = _seed_match(db_session)
    upcoming = load_next_upcoming_round(db_session)
    provider = FakeProvider(events=[], quotes_by_event={}, available=False)
    report = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS)
    assert report.provider_available is False
    assert report.quotes_created == 0


def test_creates_quote_for_resolved_event_and_player(db_session):
    match, home, away, player = _seed_match(db_session)
    upcoming = load_next_upcoming_round(db_session)
    event = _event(match)
    provider = FakeProvider(events=[event], quotes_by_event={"evt1": [_quote()]})

    report = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS)

    assert report.quotes_created == 1
    row = db_session.query(PlayerPropMarket).one()
    assert row.player_id == player.id
    assert row.match_id == match.id
    assert row.source == "the_odds_api"
    assert row.selection == "over"
    assert row.threshold == 29.5


def test_idempotent_rerun_creates_no_duplicate(db_session):
    match, home, away, player = _seed_match(db_session)
    upcoming = load_next_upcoming_round(db_session)
    event = _event(match)
    fixed_update = datetime.now(timezone.utc)
    provider = FakeProvider(events=[event], quotes_by_event={"evt1": [_quote(last_update=fixed_update)]})

    run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, force=True)
    report2 = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, force=True)

    assert report2.quotes_created == 0
    assert report2.quotes_unchanged == 1
    assert db_session.query(PlayerPropMarket).count() == 1


def test_price_change_creates_new_snapshot_not_overwrite(db_session):
    match, home, away, player = _seed_match(db_session)
    upcoming = load_next_upcoming_round(db_session)
    event = _event(match)
    t1 = datetime.now(timezone.utc) - timedelta(hours=1)
    t2 = datetime.now(timezone.utc)

    provider1 = FakeProvider(events=[event], quotes_by_event={"evt1": [_quote(price=1.9, last_update=t1)]})
    run_prop_odds_refresh(db_session, provider1, upcoming, MARKET_KEYS, force=True)

    provider2 = FakeProvider(events=[event], quotes_by_event={"evt1": [_quote(price=2.0, last_update=t2)]})
    report2 = run_prop_odds_refresh(db_session, provider2, upcoming, MARKET_KEYS, force=True)

    assert report2.quotes_created == 1
    rows = db_session.query(PlayerPropMarket).order_by(PlayerPropMarket.recorded_at).all()
    assert len(rows) == 2  # both snapshots preserved for movement history
    assert {r.price_decimal for r in rows} == {1.9, 2.0}


def test_quota_protection_skips_recently_refreshed_match(db_session):
    match, home, away, player = _seed_match(db_session)
    upcoming = load_next_upcoming_round(db_session)
    event = _event(match)
    provider = FakeProvider(events=[event], quotes_by_event={"evt1": [_quote()]})

    run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, force=True)
    assert provider.fetch_calls == ["evt1"]

    report2 = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, min_refresh_interval=timedelta(hours=1))
    assert report2.matches_skipped_fresh == 1
    assert provider.fetch_calls == ["evt1"]  # no second fetch


def test_force_bypasses_quota_protection(db_session):
    match, home, away, player = _seed_match(db_session)
    upcoming = load_next_upcoming_round(db_session)
    event = _event(match)
    provider = FakeProvider(events=[event], quotes_by_event={"evt1": [_quote()]})

    run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, force=True)
    run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, force=True)
    assert provider.fetch_calls == ["evt1", "evt1"]


def test_unresolved_player_name_reported_and_no_row_created(db_session):
    match, home, away, player = _seed_match(db_session)
    upcoming = load_next_upcoming_round(db_session)
    event = _event(match)
    provider = FakeProvider(events=[event], quotes_by_event={"evt1": [_quote(player_name="Nobody Real")]})

    report = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS)
    assert report.quotes_created == 0
    assert len(report.unresolved_players) == 1
    assert db_session.query(PlayerPropMarket).count() == 0


def test_ambiguous_player_name_reported_and_no_row_created(db_session):
    match, home, away, player = _seed_match(db_session)
    db_session.add(Player(sport_id=player.sport_id, display_name="Nick Daicos", source="afltables", source_player_id="p2", current_team_id=away.id))
    db_session.commit()
    upcoming = load_next_upcoming_round(db_session)
    event = _event(match)
    provider = FakeProvider(events=[event], quotes_by_event={"evt1": [_quote()]})

    report = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS)
    assert report.quotes_created == 0
    assert len(report.ambiguous_players) == 1


def test_unsupported_market_reported_and_no_row_created(db_session):
    match, home, away, player = _seed_match(db_session)
    upcoming = load_next_upcoming_round(db_session)
    event = _event(match)
    provider = FakeProvider(events=[event], quotes_by_event={"evt1": [_quote(market_key="player_marks_over", selection="Over", threshold=6.5)]})

    report = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS)
    assert report.quotes_created == 0
    assert len(report.unsupported_markets) == 1


def test_unresolvable_event_reported_and_skipped(db_session):
    match, *_ = _seed_match(db_session)
    upcoming = load_next_upcoming_round(db_session)
    bad_event = ProviderEvent(
        provider="the_odds_api", event_id="evt-bad", sport_key="aussierules_afl",
        home_team="Nonexistent Team", away_team="Also Fake", commence_time=match.scheduled_start,
    )
    provider = FakeProvider(events=[bad_event], quotes_by_event={})

    report = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS)
    assert len(report.matches_unresolved) == 1
    assert provider.fetch_calls == []  # never spent quota on an unresolved event


def test_manual_quote_preserved_alongside_automated_quote(db_session):
    from app.models import Bookmaker

    match, home, away, player = _seed_match(db_session)
    bookmaker = Bookmaker(name="TAB")
    db_session.add(bookmaker)
    db_session.flush()
    manual_row = PlayerPropMarket(
        match_id=match.id, player_id=player.id, bookmaker_id=bookmaker.id, market_type="player_disposals",
        line_type="over_under", threshold=27.5, selection=None, price_decimal=1.95, recorded_at=datetime.now(timezone.utc), source="manual",
    )
    db_session.add(manual_row)
    db_session.commit()

    upcoming = load_next_upcoming_round(db_session)
    event = _event(match)
    provider = FakeProvider(events=[event], quotes_by_event={"evt1": [_quote()]})
    run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS)

    rows = db_session.query(PlayerPropMarket).all()
    sources = {r.source for r in rows}
    assert sources == {"manual", "the_odds_api"}
    assert db_session.get(PlayerPropMarket, manual_row.id) is not None  # never deleted
