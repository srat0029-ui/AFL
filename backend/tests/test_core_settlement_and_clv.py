"""The sport-agnostic settlement and CLV arithmetic in app/core."""

import pytest

from app.core.clv import line_moved, price_clv, probability_clv
from app.core.settlement import (
    LINE_TYPE_MULTI_PLUS,
    LINE_TYPE_OVER_UNDER,
    RESULT_LOST,
    RESULT_PUSH,
    RESULT_UNRESOLVED,
    RESULT_WON,
    settle_line,
    settle_selection,
)
from app.player_modelling import prop_settlement
from app.player_modelling.market import LineType


@pytest.mark.parametrize(
    "actual, threshold, line_type, expected",
    [
        (25, 24.5, LINE_TYPE_OVER_UNDER, RESULT_WON),
        (24, 24.5, LINE_TYPE_OVER_UNDER, RESULT_LOST),
        (25, 25, LINE_TYPE_OVER_UNDER, RESULT_PUSH),
        (25, 25, LINE_TYPE_MULTI_PLUS, RESULT_WON),
        (24, 25, LINE_TYPE_MULTI_PLUS, RESULT_LOST),
        (25, 25, "something_else", RESULT_UNRESOLVED),
    ],
)
def test_settle_line(actual, threshold, line_type, expected):
    assert settle_line(actual, threshold, line_type) == expected


@pytest.mark.parametrize(
    "actual, selection, expected",
    [
        (25, "over", RESULT_WON),
        (25, "under", RESULT_LOST),
        (24, "under", RESULT_WON),
        (24, "over", RESULT_LOST),
    ],
)
def test_settle_selection_under_mirrors_over(actual, selection, expected):
    assert settle_selection(actual, 24.5, LINE_TYPE_OVER_UNDER, selection) == expected


def test_settle_selection_push_is_a_push_for_both_sides():
    assert settle_selection(25, 25, LINE_TYPE_OVER_UNDER, "over") == RESULT_PUSH
    assert settle_selection(25, 25, LINE_TYPE_OVER_UNDER, "under") == RESULT_PUSH


def test_settle_selection_refuses_combinations_no_bookmaker_offers():
    assert settle_selection(25, 20, LINE_TYPE_MULTI_PLUS, "under") == RESULT_UNRESOLVED
    assert settle_selection(25, 24.5, LINE_TYPE_OVER_UNDER, "sideways") == RESULT_UNRESOLVED
    assert settle_selection(25, 20, LINE_TYPE_MULTI_PLUS, "yes") == RESULT_WON


def test_afl_settlement_uses_the_shared_vocabulary_and_arithmetic():
    """AFL's module must keep exposing the same names (the AFL codebase
    imports them from there) and must agree with the shared implementation
    for AFL's own line-type enum values."""
    assert prop_settlement.RESULT_WON is RESULT_WON
    assert prop_settlement.RESULT_PUSH is RESULT_PUSH
    assert LineType.OVER_UNDER.value == LINE_TYPE_OVER_UNDER
    assert LineType.MULTI_PLUS.value == LINE_TYPE_MULTI_PLUS
    for actual in (19, 20, 21):
        for line_type in (LineType.OVER_UNDER.value, LineType.MULTI_PLUS.value):
            assert prop_settlement._settle_result(actual, 20, line_type) == settle_line(actual, 20, line_type)


def test_price_clv():
    assert price_clv(2.10, 2.00) == pytest.approx(0.05)
    assert price_clv(1.90, 2.00) == pytest.approx(-0.05)
    assert price_clv(2.00, None) is None
    with pytest.raises(ValueError):
        price_clv(2.00, 1.0)


def test_probability_clv():
    # Entry at 2.00 needs 50% to break even; the close says 54%.
    assert probability_clv(2.00, 0.54) == pytest.approx(0.04)
    assert probability_clv(2.00, None) is None
    with pytest.raises(ValueError):
        probability_clv(2.00, 1.2)


def test_line_moved():
    assert line_moved(24.5, 25.5) is True
    assert line_moved(24.5, 24.5) is False
    assert line_moved(24.5, None) is None
