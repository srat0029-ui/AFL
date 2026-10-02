"""app/nba/asof.py — the information boundary. Every function must return
only what was known at the cutoff."""

from datetime import timedelta

import pytest

from app.core.prospective import InformationLeakError, ensure_information_available
from app.models.nba import NbaAvailabilityStatus, NbaGameStatus, NbaPlayerAvailabilityReport
from app.nba.asof import GAME_RESULT_AVAILABILITY_LAG, availability_known_at, game_logs_known_at, latest_quotes_known_at
from app.nba.markets import NbaPropMarket
from tests.nba_helpers import TIPOFF, add_game_log, add_quote, seed_bookmaker, seed_game, seed_player


def test_ensure_information_available():
    ensure_information_available(TIPOFF, TIPOFF, what="x")
    ensure_information_available(TIPOFF.replace(tzinfo=None), TIPOFF, what="naive datetimes are read as UTC")
    with pytest.raises(InformationLeakError, match="injury report"):
        ensure_information_available(TIPOFF + timedelta(seconds=1), TIPOFF, what="injury report")


def test_game_logs_known_at_excludes_games_not_yet_finished_at_the_cutoff(db_session):
    earlier, home, away = seed_game(db_session, tipoff=TIPOFF - timedelta(days=2), status=NbaGameStatus.FINAL, source_game_id="g0")
    tonight, _, _ = seed_game(db_session, tipoff=TIPOFF, status=NbaGameStatus.FINAL, source_game_id="g1")
    player = seed_player(db_session, home)
    add_game_log(db_session, game=earlier, player=player, team=home, opponent=away, points=18)
    add_game_log(db_session, game=tonight, player=player, team=home, opponent=away, points=31)

    # Predicting tonight's game: tonight's own box score must not be visible,
    # even though the row exists in the table (as it would in a backtest).
    assert [log.points for log in game_logs_known_at(db_session, player.id, TIPOFF - timedelta(hours=1))] == [18]
    # Nor while tonight's game is still plausibly in progress.
    assert [log.points for log in game_logs_known_at(db_session, player.id, TIPOFF + timedelta(hours=2))] == [18]
    assert [log.points for log in game_logs_known_at(db_session, player.id, TIPOFF + GAME_RESULT_AVAILABILITY_LAG)] == [18, 31]


def test_game_logs_known_at_ignores_games_that_are_not_final(db_session):
    game, home, away = seed_game(db_session, tipoff=TIPOFF - timedelta(days=2), status=NbaGameStatus.POSTPONED)
    player = seed_player(db_session, home)
    add_game_log(db_session, game=game, player=player, team=home, opponent=away, points=18)
    assert game_logs_known_at(db_session, player.id, TIPOFF) == []


def test_availability_known_at_uses_when_we_observed_it_not_when_it_was_published(db_session):
    game, home, _ = seed_game(db_session)
    player = seed_player(db_session, home)

    def report(status, observed_at, published_at=None):
        row = NbaPlayerAvailabilityReport(
            player_id=player.id, team_id=home.id, game_id=game.id, status=status.value, source_status=status.value.title(), source="test",
            observed_at=observed_at, source_published_at=published_at,
        )
        db_session.add(row)
        db_session.flush()
        return row

    report(NbaAvailabilityStatus.QUESTIONABLE, TIPOFF - timedelta(hours=8))
    # Published at T-5h, but we only fetched it at T-1h.
    report(NbaAvailabilityStatus.OUT, TIPOFF - timedelta(hours=1), published_at=TIPOFF - timedelta(hours=5))

    assert availability_known_at(db_session, player.id, TIPOFF - timedelta(hours=9)) is None
    assert availability_known_at(db_session, player.id, TIPOFF - timedelta(hours=3)).status == "questionable"
    assert availability_known_at(db_session, player.id, TIPOFF - timedelta(minutes=30), game_id=game.id).status == "out"


def test_latest_quotes_known_at_returns_one_row_per_bookmaker_line_and_side(db_session):
    game, home, _ = seed_game(db_session)
    player = seed_player(db_session, home)
    book_a, book_b = seed_bookmaker(db_session, "Book A"), seed_bookmaker(db_session, "Book B")
    t = TIPOFF - timedelta(hours=6)
    add_quote(db_session, game=game, player=player, bookmaker=book_a, observed_at=t, price=1.90)
    add_quote(db_session, game=game, player=player, bookmaker=book_a, observed_at=t + timedelta(hours=1), price=1.95)
    add_quote(db_session, game=game, player=player, bookmaker=book_a, observed_at=t + timedelta(hours=3), price=2.05)
    add_quote(db_session, game=game, player=player, bookmaker=book_b, observed_at=t, price=1.87, selection="under")
    add_quote(db_session, game=game, player=player, bookmaker=book_a, observed_at=t, price=1.80, market=NbaPropMarket.REBOUNDS, line=7.5)

    def prices(cutoff, **kwargs):
        quotes = latest_quotes_known_at(db_session, game_id=game.id, player_id=player.id, market="player_points", cutoff=cutoff, **kwargs)
        return sorted((q.bookmaker_id, q.selection, q.price_decimal) for q in quotes)

    assert prices(t - timedelta(minutes=1)) == []
    assert prices(t + timedelta(hours=2)) == [(book_a.id, "over", 1.95), (book_b.id, "under", 1.87)]
    assert prices(t + timedelta(hours=4)) == [(book_a.id, "over", 2.05), (book_b.id, "under", 1.87)]
    assert prices(t + timedelta(hours=4), max_age=timedelta(hours=2)) == [(book_a.id, "over", 2.05)]


def test_latest_quotes_known_at_drops_a_superseded_main_line_but_keeps_alternates(db_session):
    game, home, _ = seed_game(db_session)
    player = seed_player(db_session, home)
    book = seed_bookmaker(db_session)
    t = TIPOFF - timedelta(hours=6)
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=t, price=1.90, line=24.5)
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=t, price=3.10, line=29.5, is_alternate_line=True)
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=t + timedelta(hours=2), price=1.90, line=25.5)

    def lines(cutoff):
        quotes = latest_quotes_known_at(db_session, game_id=game.id, player_id=player.id, market="player_points", cutoff=cutoff)
        return sorted(q.line for q in quotes)

    assert lines(t + timedelta(hours=1)) == [24.5, 29.5]
    assert lines(t + timedelta(hours=3)) == [25.5, 29.5]
