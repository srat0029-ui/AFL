"""The NBA prospective pipeline: project -> freeze before tip-off -> capture
the closing line once -> settle once. These tests are about the RULES
(what may be written when, and that it can never be rewritten), exercised
with synthetic rows."""

from datetime import timedelta

import pytest

from app.core.clv import price_clv, probability_clv
from app.core.prospective import FrozenRecordError, InformationLeakError
from app.models.bookmaker import ELIGIBILITY_INFORMATIONAL
from app.models.nba import NbaGameStatus, NbaPropPrediction
from app.nba.markets import NbaPropMarket
from app.nba.prospective import (
    SETTLE_ALREADY_SETTLED,
    SETTLE_AWAITING_RESULT,
    SETTLE_SETTLED,
    capture_closing_line,
    capture_due_closing_lines,
    freeze_prediction,
    record_projection,
    settle_due_predictions,
    settle_prediction,
)
from tests.nba_helpers import POINTS_DISTRIBUTION, TIPOFF, add_game_log, add_quote, projection_output, seed_bookmaker, seed_game, seed_player

PREDICT_AT = TIPOFF - timedelta(hours=3)


def _setup(db):
    game, home, away = seed_game(db)
    player = seed_player(db, home)
    return game, home, away, player


def _project(db, game, home, player, *, now=PREDICT_AT, cutoff=None, market=NbaPropMarket.POINTS):
    return record_projection(
        db, game=game, player_id=player.id, team_id=home.id, model_name="test_model", model_version="v1",
        information_cutoff=cutoff or now, output=projection_output(market), now=now,
    )


def _freeze(db, projection, *, line=24.5, selection="over", now=PREDICT_AT, **kwargs):
    return freeze_prediction(db, projection=projection, distribution=POINTS_DISTRIBUTION, line=line, selection=selection, now=now, **kwargs)


# --- projection ------------------------------------------------------------


def test_projection_records_the_minutes_rate_decomposition(db_session):
    game, home, _, player = _setup(db_session)
    projection = _project(db_session, game, home, player)
    assert projection.predicted_mean == pytest.approx(25.0)
    assert projection.expected_minutes == 32.0
    assert projection.expected_minutes * projection.rate_per_minute == pytest.approx(projection.predicted_mean)


def test_projection_is_idempotent_and_a_later_cutoff_is_a_new_row(db_session):
    game, home, _, player = _setup(db_session)
    first = _project(db_session, game, home, player)
    assert _project(db_session, game, home, player).id == first.id
    later = _project(db_session, game, home, player, now=PREDICT_AT + timedelta(hours=1))
    assert later.id != first.id


def test_projection_rejected_at_or_after_tipoff(db_session):
    game, home, _, player = _setup(db_session)
    with pytest.raises(InformationLeakError):
        _project(db_session, game, home, player, now=TIPOFF)


def test_projection_rejected_when_its_information_cutoff_is_in_the_future(db_session):
    game, home, _, player = _setup(db_session)
    with pytest.raises(InformationLeakError):
        _project(db_session, game, home, player, now=PREDICT_AT, cutoff=PREDICT_AT + timedelta(minutes=1))


def test_projection_rejected_for_a_game_that_is_no_longer_scheduled(db_session):
    game, home, _, player = _setup(db_session)
    game.status = NbaGameStatus.FINAL.value
    with pytest.raises(InformationLeakError):
        _project(db_session, game, home, player)


# --- entry -----------------------------------------------------------------


def test_freeze_takes_best_eligible_price_and_devigged_consensus(db_session):
    game, home, _, player = _setup(db_session)
    book_a, book_b = seed_bookmaker(db_session, "Book A"), seed_bookmaker(db_session, "Book B")
    exchange = seed_bookmaker(db_session, "Exchange", eligibility=ELIGIBILITY_INFORMATIONAL)
    seen = PREDICT_AT - timedelta(minutes=10)
    add_quote(db_session, game=game, player=player, bookmaker=book_a, observed_at=seen, price=1.90, selection="over")
    add_quote(db_session, game=game, player=player, bookmaker=book_a, observed_at=seen, price=1.90, selection="under")
    best = add_quote(db_session, game=game, player=player, bookmaker=book_b, observed_at=seen, price=2.00, selection="over")
    add_quote(db_session, game=game, player=player, bookmaker=book_b, observed_at=seen, price=1.80, selection="under")
    # An exchange price is never "the" best price, however good it looks.
    add_quote(db_session, game=game, player=player, bookmaker=exchange, observed_at=seen, price=2.60, selection="over")

    prediction = _freeze(db_session, _project(db_session, game, home, player))

    assert prediction.model_probability == pytest.approx(0.6)
    assert prediction.model_fair_odds == pytest.approx(1 / 0.6)
    assert prediction.entry_bookmaker_id == book_b.id
    assert prediction.entry_quote_id == best.id
    assert prediction.entry_price == 2.00
    assert prediction.entry_expected_value == pytest.approx(0.6 * 1.0 - 0.4)
    # Book A de-vigs to 0.5; Book B to (1/2.0) / (1/2.0 + 1/1.8).
    book_b_fair = (1 / 2.0) / (1 / 2.0 + 1 / 1.8)
    assert prediction.entry_consensus_probability == pytest.approx((0.5 + book_b_fair) / 2)
    assert prediction.entry_n_bookmakers == 2
    assert prediction.closing_captured_at is None and prediction.settled_at is None


def test_freeze_under_uses_the_distributions_under_probability(db_session):
    game, home, _, player = _setup(db_session)
    projection = _project(db_session, game, home, player)
    # On the whole-number line 25 there is a 20% push: over 0.4, under 0.4.
    assert _freeze(db_session, projection, line=25, selection="under").model_probability == pytest.approx(0.4)
    assert _freeze(db_session, projection, line=25, selection="over").model_probability == pytest.approx(0.4)


def test_freeze_ignores_quotes_observed_after_the_prediction_instant(db_session):
    game, home, _, player = _setup(db_session)
    book = seed_bookmaker(db_session)
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=PREDICT_AT - timedelta(minutes=5), price=1.85)
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=PREDICT_AT + timedelta(minutes=5), price=2.40)

    prediction = _freeze(db_session, _project(db_session, game, home, player))
    assert prediction.entry_price == 1.85


def test_freeze_without_any_market_still_freezes_the_model_side(db_session):
    game, home, _, player = _setup(db_session)
    prediction = _freeze(db_session, _project(db_session, game, home, player))
    assert prediction.model_probability == pytest.approx(0.6)
    assert prediction.entry_price is None and prediction.entry_bookmaker_id is None
    assert prediction.entry_consensus_probability is None and prediction.entry_expected_value is None


def test_freeze_one_sided_quote_gives_a_price_but_no_consensus(db_session):
    game, home, _, player = _setup(db_session)
    book = seed_bookmaker(db_session)
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=PREDICT_AT - timedelta(minutes=5), price=1.95)
    prediction = _freeze(db_session, _project(db_session, game, home, player))
    assert prediction.entry_price == 1.95
    assert prediction.entry_consensus_probability is None and prediction.entry_n_bookmakers is None


def test_freeze_respects_max_quote_age(db_session):
    game, home, _, player = _setup(db_session)
    book = seed_bookmaker(db_session)
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=PREDICT_AT - timedelta(hours=10), price=1.95)
    prediction = _freeze(db_session, _project(db_session, game, home, player), max_quote_age=timedelta(hours=2))
    assert prediction.entry_price is None


def test_freeze_is_idempotent_and_does_not_reprice(db_session):
    game, home, _, player = _setup(db_session)
    book = seed_bookmaker(db_session)
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=PREDICT_AT - timedelta(minutes=30), price=1.85)
    projection = _project(db_session, game, home, player)
    first = _freeze(db_session, projection)

    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=PREDICT_AT + timedelta(minutes=20), price=2.20)
    again = _freeze(db_session, projection, now=PREDICT_AT + timedelta(minutes=30))

    assert again.id == first.id
    assert again.entry_price == 1.85 and again.predicted_at.replace(tzinfo=None) == PREDICT_AT.replace(tzinfo=None)
    assert db_session.query(NbaPropPrediction).count() == 1


def test_freeze_rejected_at_or_after_tipoff(db_session):
    game, home, _, player = _setup(db_session)
    projection = _project(db_session, game, home, player)
    with pytest.raises(InformationLeakError):
        _freeze(db_session, projection, now=TIPOFF)
    with pytest.raises(InformationLeakError):
        _freeze(db_session, projection, now=TIPOFF + timedelta(hours=1))
    assert db_session.query(NbaPropPrediction).count() == 0


def test_freeze_rejects_unknown_selection_and_unpriceable_probability(db_session):
    game, home, _, player = _setup(db_session)
    projection = _project(db_session, game, home, player)
    with pytest.raises(ValueError):
        _freeze(db_session, projection, selection="yes")
    with pytest.raises(ValueError):
        _freeze(db_session, projection, line=99.5, selection="over")  # probability 0


# --- closing line ----------------------------------------------------------


def _frozen_with_market(db):
    game, home, away, player = _setup(db)
    book = seed_bookmaker(db)
    seen = PREDICT_AT - timedelta(minutes=10)
    add_quote(db, game=game, player=player, bookmaker=book, observed_at=seen, price=2.10, selection="over")
    add_quote(db, game=game, player=player, bookmaker=book, observed_at=seen, price=1.75, selection="under")
    prediction = _freeze(db, _project(db, game, home, player))
    db.commit()
    return game, home, away, player, book, prediction


def test_closing_line_cannot_be_captured_before_tipoff(db_session):
    *_, prediction = _frozen_with_market(db_session)
    with pytest.raises(InformationLeakError):
        capture_closing_line(db_session, prediction, now=TIPOFF - timedelta(minutes=1))
    assert prediction.closing_captured_at is None


def test_closing_line_is_the_last_quote_at_or_before_tipoff(db_session):
    game, _, _, player, book, prediction = _frozen_with_market(db_session)
    close = TIPOFF - timedelta(minutes=2)
    closing_over = add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=close, price=2.00, selection="over")
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=close, price=1.80, selection="under")
    # In-play price after tip-off: never the closing line.
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=TIPOFF + timedelta(minutes=10), price=3.50, selection="over")

    assert capture_closing_line(db_session, prediction, now=TIPOFF + timedelta(hours=1)) is True
    db_session.commit()

    assert prediction.closing_price == 2.00
    assert prediction.closing_quote_id == closing_over.id
    assert prediction.closing_main_line == 24.5
    assert prediction.closing_consensus_probability == pytest.approx((1 / 2.0) / (1 / 2.0 + 1 / 1.8))
    assert prediction.closing_n_bookmakers == 1
    # Entry untouched, and CLV follows from the two frozen sides.
    assert prediction.entry_price == 2.10
    assert price_clv(prediction.entry_price, prediction.closing_price) == pytest.approx(0.05)
    # Beating the same book's closing PRICE is not the same as beating the
    # closing FAIR probability - 2.10 still needs 47.6% and the de-vigged
    # close says 47.4%. Both views are kept for exactly this reason.
    assert probability_clv(prediction.entry_price, prediction.closing_consensus_probability) == pytest.approx(0.473684 - 1 / 2.10, abs=1e-5)


def test_closing_line_when_the_main_line_moved(db_session):
    game, _, _, player, book, prediction = _frozen_with_market(db_session)
    close = TIPOFF - timedelta(minutes=2)
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=close, price=1.90, selection="over", line=26.5)
    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=close, price=1.90, selection="under", line=26.5)

    capture_closing_line(db_session, prediction, now=TIPOFF + timedelta(hours=1))

    # The 24.5 main line was superseded, so there is no like-for-like closing
    # price - but the move itself is recorded.
    assert prediction.closing_main_line == 26.5
    assert prediction.closing_price is None
    assert prediction.closing_consensus_probability is None
    assert prediction.closing_captured_at is not None


def test_closing_line_is_captured_exactly_once(db_session):
    game, _, _, player, book, prediction = _frozen_with_market(db_session)
    assert capture_closing_line(db_session, prediction, now=TIPOFF + timedelta(hours=1)) is True
    db_session.commit()
    first_price = prediction.closing_price

    add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=TIPOFF - timedelta(seconds=30), price=1.50, selection="over")
    assert capture_closing_line(db_session, prediction, now=TIPOFF + timedelta(hours=2)) is False
    assert prediction.closing_price == first_price


def test_closing_line_not_captured_for_a_postponed_game(db_session):
    game, *_, prediction = _frozen_with_market(db_session)
    game.status = NbaGameStatus.POSTPONED.value
    assert capture_closing_line(db_session, prediction, now=TIPOFF + timedelta(hours=1)) is False
    assert prediction.closing_captured_at is None


def test_capture_due_closing_lines_only_touches_tipped_off_games(db_session):
    *_, prediction = _frozen_with_market(db_session)
    assert capture_due_closing_lines(db_session, now=TIPOFF - timedelta(minutes=30)).considered == 0
    report = capture_due_closing_lines(db_session, now=TIPOFF + timedelta(minutes=30))
    assert (report.considered, report.captured) == (1, 1)
    assert capture_due_closing_lines(db_session, now=TIPOFF + timedelta(hours=3)).considered == 0
    assert prediction.closing_captured_at is not None


# --- settlement ------------------------------------------------------------


def test_settlement_waits_until_the_game_is_final_and_the_box_score_exists(db_session):
    game, *_, prediction = _frozen_with_market(db_session)
    assert settle_prediction(db_session, prediction) == SETTLE_AWAITING_RESULT
    game.status = NbaGameStatus.FINAL.value
    assert settle_prediction(db_session, prediction) == SETTLE_AWAITING_RESULT
    assert prediction.settled_at is None and prediction.outcome is None


@pytest.mark.parametrize("points, selection, expected", [(30, "over", "won"), (20, "over", "lost"), (20, "under", "won"), (30, "under", "lost")])
def test_settlement_result(db_session, points, selection, expected):
    game, home, away, player = _setup(db_session)
    prediction = _freeze(db_session, _project(db_session, game, home, player), selection=selection)
    game.status = NbaGameStatus.FINAL.value
    add_game_log(db_session, game=game, player=player, team=home, opponent=away, points=points)

    assert settle_prediction(db_session, prediction) == SETTLE_SETTLED
    assert prediction.outcome == expected
    assert prediction.actual_value == points


def test_settlement_push_on_a_whole_number_line(db_session):
    game, home, away, player = _setup(db_session)
    prediction = _freeze(db_session, _project(db_session, game, home, player), line=25)
    game.status = NbaGameStatus.FINAL.value
    add_game_log(db_session, game=game, player=player, team=home, opponent=away, points=25)
    settle_prediction(db_session, prediction)
    assert prediction.outcome == "push"


def test_settlement_voids_a_player_who_did_not_play(db_session):
    game, home, away, player = _setup(db_session)
    other = seed_player(db_session, home, name="Other Player", source_player_id="p2")
    projection = _project(db_session, game, home, player)
    dnp = _freeze(db_session, projection, selection="over")
    no_row = _freeze(db_session, _project(db_session, game, home, other), selection="over")
    game.status = NbaGameStatus.FINAL.value
    add_game_log(db_session, game=game, player=player, team=home, opponent=away, minutes=None, did_not_play=True)

    assert settle_prediction(db_session, dnp) == SETTLE_SETTLED
    assert dnp.outcome == "void" and dnp.actual_value is None
    # The box score has been ingested (a row exists for the game) and this
    # player is not in it: void, not pending and not a loss.
    assert settle_prediction(db_session, no_row) == SETTLE_SETTLED
    assert no_row.outcome == "void"


def test_settlement_voids_a_cancelled_game(db_session):
    game, *_, prediction = _frozen_with_market(db_session)
    game.status = NbaGameStatus.CANCELLED.value
    assert settle_prediction(db_session, prediction) == SETTLE_SETTLED
    assert prediction.outcome == "void"


def test_settlement_happens_exactly_once_even_if_the_box_score_is_later_corrected(db_session):
    game, home, away, player = _setup(db_session)
    prediction = _freeze(db_session, _project(db_session, game, home, player))
    game.status = NbaGameStatus.FINAL.value
    log = add_game_log(db_session, game=game, player=player, team=home, opponent=away, points=30)
    settle_prediction(db_session, prediction)
    db_session.commit()

    log.points = 20  # a later stat correction
    db_session.commit()
    assert settle_prediction(db_session, prediction) == SETTLE_ALREADY_SETTLED
    assert prediction.outcome == "won" and prediction.actual_value == 30


def test_settle_due_predictions_reports_counts(db_session):
    game, home, away, player = _setup(db_session)
    projection = _project(db_session, game, home, player)
    _freeze(db_session, projection, selection="over")
    _freeze(db_session, projection, selection="under")
    assert settle_due_predictions(db_session).awaiting_result == 2

    game.status = NbaGameStatus.FINAL.value
    add_game_log(db_session, game=game, player=player, team=home, opponent=away, points=30)
    report = settle_due_predictions(db_session)
    assert (report.settled, report.won, report.lost) == (2, 1, 1)
    assert settle_due_predictions(db_session).considered == 0


# --- the write-once guard, through the ORM ---------------------------------


def test_entry_fields_cannot_be_rewritten_after_the_freeze(db_session):
    *_, prediction = _frozen_with_market(db_session)
    for field, value in [("model_probability", 0.99), ("entry_price", 5.0), ("line", 10.5), ("information_cutoff", TIPOFF)]:
        setattr(prediction, field, value)
        with pytest.raises(FrozenRecordError):
            db_session.commit()
        db_session.rollback()
    db_session.refresh(prediction)
    assert prediction.model_probability == pytest.approx(0.6) and prediction.entry_price == 2.10


def test_closing_and_settlement_fields_cannot_be_rewritten_once_written(db_session):
    game, home, away, player, _, prediction = _frozen_with_market(db_session)
    capture_closing_line(db_session, prediction, now=TIPOFF + timedelta(hours=1))
    game.status = NbaGameStatus.FINAL.value
    add_game_log(db_session, game=game, player=player, team=home, opponent=away, points=30)
    settle_prediction(db_session, prediction)
    db_session.commit()

    for field, value in [("closing_price", 9.0), ("closing_consensus_probability", 0.1), ("outcome", "lost"), ("actual_value", 1.0)]:
        setattr(prediction, field, value)
        with pytest.raises(FrozenRecordError):
            db_session.commit()
        db_session.rollback()
    db_session.refresh(prediction)
    assert prediction.outcome == "won"


def test_frozen_rows_cannot_be_deleted(db_session):
    *_, prediction = _frozen_with_market(db_session)
    for row in (prediction, prediction.projection):
        db_session.delete(row)
        with pytest.raises(FrozenRecordError):
            db_session.commit()
        db_session.rollback()


def test_projections_and_quotes_are_fully_frozen(db_session):
    game, _, _, player, book, prediction = _frozen_with_market(db_session)
    projection = prediction.projection
    projection.predicted_mean = 99.0
    with pytest.raises(FrozenRecordError):
        db_session.commit()
    db_session.rollback()

    quote = add_quote(db_session, game=game, player=player, bookmaker=book, observed_at=PREDICT_AT, price=2.0)
    db_session.commit()
    quote.price_decimal = 3.0
    with pytest.raises(FrozenRecordError):
        db_session.commit()
    db_session.rollback()
