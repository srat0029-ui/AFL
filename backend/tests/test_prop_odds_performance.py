"""Performance/correctness regression tests for the prop-odds ingestion
fast path (app/player_modelling/prop_odds_ingestion.py,
app/player_modelling/prop_player_resolution.py).

Context: a real production Live Cycle run was killed by its 30-minute
workflow timeout inside run_prop_odds_refresh. Profiling traced it to
~10 SQL queries issued PER QUOTE (player resolution + a duplicate-existence
check + a bookmaker lookup) - ~27,700 queries for a single match's ~2,800
realistic quotes. The fix preloads a MatchResolutionContext and the
match's existing PlayerPropMarket rows ONCE per match, then resolves and
identity-checks every quote purely in memory.

test_query_count_is_not_proportional_to_quote_count is the specific
regression guard requested: it fails loudly if this N+1 pattern quietly
returns, by asserting the query count for a MUCH larger batch stays within
a small, fixed ceiling rather than scaling with quote count.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select

from app.models import Match, MatchStatus, Player, PlayerPropMarket, Round, Season, Sport, Team
from app.player_modelling.prop_odds_ingestion import run_prop_odds_refresh
from app.player_modelling.prop_player_resolution import build_match_resolution_context, resolve_prop_player, resolve_prop_player_with_context
from app.player_modelling.upcoming_features import load_next_upcoming_round
from app.providers.player_prop_odds import PlayerPropOddsProvider, PlayerPropOddsResult, QuotaStatus
from app.providers.types import PlayerPropQuote, ProviderEvent

MARKET_KEYS = ["player_disposals", "player_goals_scored_over"]
NAMES = [
    ("Nick", "Daicos"), ("Cam", "Rayner"), ("Lachie", "Schultz"), ("Marcus", "Bontempelli"),
    ("Patrick", "Cripps"), ("Christian", "Petracca"), ("Clayton", "Oliver"), ("Jeremy", "Cameron"),
    ("Tom", "Hawkins"), ("Charlie", "Curnow"), ("Zach", "Merrett"), ("Dustin", "Martin"),
]


class FakeProvider(PlayerPropOddsProvider):
    def __init__(self, events, quotes_by_event):
        self._events = events
        self._quotes_by_event = quotes_by_event
        self.fetch_calls = []

    @property
    def is_available(self) -> bool:
        return True

    def list_events(self, sport_code):
        return self._events

    def get_player_prop_quotes(self, sport_code, event, market_keys):
        self.fetch_calls.append(event.event_id)
        return PlayerPropOddsResult(quotes=self._quotes_by_event.get(event.event_id, []), quota=QuotaStatus(requests_used=1, requests_remaining=499), markets_returned=market_keys)


@contextmanager
def count_queries(session):
    counter = {"n": 0}
    engine = session.get_bind()

    def _before_cursor_execute(*args, **kwargs):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)


def _seed_match(db, round_number=1, home_name="Collingwood", away_name="Carlton", event_id="evt1", round_=None):
    sport = db.scalar(select(Sport).where(Sport.code == "AFL"))
    if sport is None:
        sport = Sport(code="AFL", name="Australian Football League")
        db.add(sport)
        db.flush()
    season = db.scalar(select(Season).where(Season.sport_id == sport.id, Season.year == 2026))
    if season is None:
        season = Season(sport_id=sport.id, year=2026)
        db.add(season)
        db.flush()
    if round_ is None:
        round_ = db.scalar(select(Round).where(Round.season_id == season.id, Round.round_number == round_number))
    if round_ is None:
        round_ = Round(season_id=season.id, round_number=round_number)
        db.add(round_)
        db.flush()
    home = Team(sport_id=sport.id, name=home_name, short_name=home_name[:3].upper())
    away = Team(sport_id=sport.id, name=away_name, short_name=away_name[:3].upper())
    db.add_all([round_, home, away])
    db.flush()
    match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime.now(timezone.utc) + timedelta(hours=6), status=MatchStatus.SCHEDULED,
        external_ids={"squiggle": event_id},
    )
    db.add(match)
    db.flush()
    players = []
    for i, (given, surname) in enumerate(NAMES):
        team_id = home.id if i % 2 == 0 else away.id
        p = Player(sport_id=sport.id, display_name=f"{given} {surname}", source="afltables", source_player_id=f"{event_id}-p{i}", current_team_id=team_id)
        db.add(p)
        players.append((p, given, surname))
    db.commit()
    return match, home, away, players


def _quote(player_name, market_key, threshold, selection, bookmaker_key, event_id, price=1.9, last_update=None):
    now = last_update or datetime.now(timezone.utc)
    return PlayerPropQuote(
        provider="the_odds_api", event_id=event_id, sport_code="AFL", bookmaker_key=bookmaker_key,
        bookmaker_title=bookmaker_key.title(), bookmaker_region="au", market_key=market_key,
        player_name=player_name, selection=selection, price_decimal=price,
        bookmaker_last_update=now, fetched_at=now, threshold=threshold,
    )


def _generate_quotes(players, event_id, n_thresholds, n_bookmakers):
    quotes = []
    bookmakers = [f"bm{i}" for i in range(n_bookmakers)]
    for player, given, surname in players:
        name = f"{given} {surname}"
        for bm in bookmakers:
            for t in range(n_thresholds):
                quotes.append(_quote(name, "player_disposals", 10.5 + t, "Over", bm, event_id))
    return quotes


def _event(match, event_id="evt1"):
    return ProviderEvent(
        provider="the_odds_api", event_id=event_id, sport_key="aussierules_afl",
        home_team=match.home_team.name, away_team=match.away_team.name, commence_time=match.scheduled_start,
    )


# --- G: query-count regression guard ---------------------------------------


def test_query_count_is_not_proportional_to_quote_count(db_session):
    """The specific regression guard: a batch 10x larger than another must
    NOT produce ~10x the SQL queries. Before this fix, query count scaled
    linearly (~10 queries/quote); after, it's a small, fixed number per
    match regardless of quote volume."""
    match, home, away, players = _seed_match(db_session)
    small_quotes = _generate_quotes(players, "evt1", n_thresholds=1, n_bookmakers=2)  # 24 quotes
    upcoming = load_next_upcoming_round(db_session)
    provider = FakeProvider(events=[_event(match)], quotes_by_event={"evt1": small_quotes})
    with count_queries(db_session) as counter:
        report = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, force=True)
    small_queries = counter["n"]
    assert report.quotes_seen == len(small_quotes)

    # Fresh match, in the SAME round (so load_next_upcoming_round picks it
    # up), 10x the quotes.
    shared_round = match.round
    match2, *_ = _seed_match(db_session, home_name="Richmond", away_name="Essendon", event_id="evt2", round_=shared_round)
    large_players = [(p, g, s) for p, g, s in players]  # reuse same name pool, different match/team ids won't matter here
    large_quotes = _generate_quotes(large_players, "evt2", n_thresholds=10, n_bookmakers=2)  # 240 quotes
    upcoming2 = load_next_upcoming_round(db_session)
    provider2 = FakeProvider(events=[_event(match2, "evt2")], quotes_by_event={"evt2": large_quotes})
    with count_queries(db_session) as counter2:
        report2 = run_prop_odds_refresh(db_session, provider2, upcoming2, MARKET_KEYS, force=True)
    large_queries = counter2["n"]
    assert report2.quotes_seen == len(large_quotes)

    # 10x the quotes must NOT mean anywhere close to 10x the queries - a
    # small constant-ish increase (context/lookup/insert overhead), not
    # O(quote count). The old code would have shown large_queries ~=
    # 10 * small_queries here.
    assert large_queries < small_queries + 30, (
        f"query count scaled with quote volume (small={small_queries} for {len(small_quotes)} quotes, "
        f"large={large_queries} for {len(large_quotes)} quotes) - the N+1 pattern may have returned"
    )
    # Absolute sanity ceiling matching the task's own stated target.
    assert large_queries < 60


def test_realistic_scale_batch_stays_well_under_query_ceiling(db_session):
    """~2,000 quotes for one match (same order of magnitude as the real
    production incident's ~3,958-quote match) must resolve in a bounded
    number of queries - "a few dozen... not tens of thousands."""
    match, home, away, players = _seed_match(db_session)
    quotes = _generate_quotes(players, "evt1", n_thresholds=42, n_bookmakers=4)  # 12 players * 42 * 4 = 2016
    upcoming = load_next_upcoming_round(db_session)
    provider = FakeProvider(events=[_event(match)], quotes_by_event={"evt1": quotes})
    with count_queries(db_session) as counter:
        report = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, force=True)
    assert report.quotes_seen == len(quotes)
    assert report.quotes_created == len(quotes)
    assert counter["n"] < 60, f"{counter['n']} queries for {len(quotes)} quotes - expected a few dozen, not thousands"


# --- G: idempotency / duplicate handling at scale --------------------------


def test_duplicate_quotes_within_one_response_do_not_create_duplicate_rows(db_session):
    match, home, away, players = _seed_match(db_session)
    player, given, surname = players[0]
    name = f"{given} {surname}"
    same_update = datetime.now(timezone.utc)
    duplicated = [
        _quote(name, "player_disposals", 20.5, "Over", "sportsbet", "evt1", price=1.9, last_update=same_update),
        _quote(name, "player_disposals", 20.5, "Over", "sportsbet", "evt1", price=1.9, last_update=same_update),
        _quote(name, "player_disposals", 20.5, "Over", "sportsbet", "evt1", price=1.9, last_update=same_update),
    ]
    upcoming = load_next_upcoming_round(db_session)
    provider = FakeProvider(events=[_event(match)], quotes_by_event={"evt1": duplicated})

    report = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, force=True)

    assert report.quotes_seen == 3
    assert report.quotes_created == 1
    assert report.quotes_unchanged == 2
    assert db_session.query(PlayerPropMarket).count() == 1


def test_second_identical_refresh_creates_zero_additional_rows_at_scale(db_session):
    match, home, away, players = _seed_match(db_session)
    quotes = _generate_quotes(players, "evt1", n_thresholds=8, n_bookmakers=3)  # fixed timestamps below
    fixed_update = datetime.now(timezone.utc)
    quotes = [
        _quote(q.player_name, q.market_key, q.threshold, q.selection, q.bookmaker_key, q.event_id, price=q.price_decimal, last_update=fixed_update)
        for q in quotes
    ]
    upcoming = load_next_upcoming_round(db_session)
    provider = FakeProvider(events=[_event(match)], quotes_by_event={"evt1": quotes})

    report1 = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, force=True)
    n_after_first = db_session.query(PlayerPropMarket).count()
    assert report1.quotes_created == len(quotes)
    assert n_after_first == len(quotes)

    with count_queries(db_session) as counter:
        report2 = run_prop_odds_refresh(db_session, provider, upcoming, MARKET_KEYS, force=True)

    assert report2.quotes_created == 0
    assert report2.quotes_unchanged == len(quotes)
    assert db_session.query(PlayerPropMarket).count() == n_after_first
    assert counter["n"] < 60  # the re-check pass must also stay batched, not re-introduce per-quote queries


# --- G: partial/batched persistence can be safely retried ------------------


def test_partial_persistence_across_two_matches_is_safely_resumable(db_session):
    """Simulates the real incident's shape: match 1 finishes and commits,
    then the process is interrupted before match 2 is processed. A later
    invocation must pick up match 2 without disturbing or duplicating
    match 1's already-committed rows."""
    match1, home1, away1, players1 = _seed_match(db_session, home_name="Collingwood", away_name="Carlton", event_id="evt1")
    match2, home2, away2, players2 = _seed_match(db_session, home_name="Richmond", away_name="Essendon", event_id="evt2", round_=match1.round)
    quotes1 = _generate_quotes(players1, "evt1", n_thresholds=3, n_bookmakers=2)
    quotes2 = _generate_quotes(players2, "evt2", n_thresholds=3, n_bookmakers=2)

    # "Process 1": only match 1's event is visible (simulating a kill
    # before match 2 was ever reached).
    upcoming = load_next_upcoming_round(db_session)
    provider_partial = FakeProvider(events=[_event(match1, "evt1")], quotes_by_event={"evt1": quotes1, "evt2": quotes2})
    report_partial = run_prop_odds_refresh(db_session, provider_partial, upcoming, MARKET_KEYS, force=True)
    assert report_partial.quotes_created == len(quotes1)
    count_after_partial = db_session.query(PlayerPropMarket).count()
    assert count_after_partial == len(quotes1)

    # "Process 2" (the resumed/next invocation): both events visible now.
    # Match 1's quotes are unchanged (identical timestamps/prices) and
    # must not be duplicated; match 2's are genuinely new.
    provider_full = FakeProvider(events=[_event(match1, "evt1"), _event(match2, "evt2")], quotes_by_event={"evt1": quotes1, "evt2": quotes2})
    report_full = run_prop_odds_refresh(db_session, provider_full, upcoming, MARKET_KEYS, force=True)

    assert report_full.quotes_created == len(quotes2)
    assert report_full.quotes_unchanged == len(quotes1)
    assert db_session.query(PlayerPropMarket).count() == len(quotes1) + len(quotes2)


# --- G: resolution equivalence between the legacy single-call path and the
# batch context path (the same claim tests/test_prop_player_resolution.py
# already proves by passing unchanged, made explicit here) -----------------


def test_context_based_and_legacy_single_call_resolution_agree(db_session):
    match, home, away, players = _seed_match(db_session)
    context = build_match_resolution_context(db_session, match)
    representative_names = [
        "Nick Daicos", "N. Daicos", "NICK DAICOS", "Nobody Real", "Cam Rayner",
    ]
    for name in representative_names:
        legacy = resolve_prop_player(db_session, match, name)
        via_context = resolve_prop_player_with_context(context, name)
        assert legacy.tier == via_context.tier, name
        legacy_id = legacy.player.id if legacy.player else None
        context_id = via_context.player.id if via_context.player else None
        assert legacy_id == context_id, name
