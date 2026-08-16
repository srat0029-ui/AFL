from datetime import datetime, timezone

from app.player_modelling.market import LineType, PlayerMarket
from app.player_modelling.prop_market_mapping import NormalizedProp, UnsupportedMarket, normalize_prop_quote
from app.providers.types import PlayerPropQuote

NOW = datetime(2026, 8, 16, tzinfo=timezone.utc)


def _quote(market_key: str, selection: str, threshold: float | None, price: float = 1.9) -> PlayerPropQuote:
    return PlayerPropQuote(
        provider="the_odds_api", event_id="evt1", sport_code="AFL", bookmaker_key="sportsbet",
        bookmaker_title="Sportsbet", bookmaker_region="au", market_key=market_key, player_name="Nick Daicos",
        selection=selection, price_decimal=price, bookmaker_last_update=NOW, fetched_at=NOW, threshold=threshold,
    )


def test_disposals_over_under_maps_to_over_under_with_provided_threshold():
    result = normalize_prop_quote(_quote("player_disposals", "Over", 29.5))
    assert isinstance(result, NormalizedProp)
    assert result.market is PlayerMarket.DISPOSALS
    assert result.line_type is LineType.OVER_UNDER
    assert result.threshold == 29.5
    assert result.selection == "over"


def test_disposals_under_side_maps_correctly():
    result = normalize_prop_quote(_quote("player_disposals", "Under", 29.5))
    assert isinstance(result, NormalizedProp)
    assert result.selection == "under"


def test_disposals_alternate_line_over_only():
    result = normalize_prop_quote(_quote("player_disposals_over", "Over", 24.5))
    assert isinstance(result, NormalizedProp)
    assert result.market is PlayerMarket.DISPOSALS
    assert result.threshold == 24.5
    assert result.selection == "over"


def test_anytime_goalscorer_maps_to_multi_plus_one():
    result = normalize_prop_quote(_quote("player_goal_scorer_anytime", "Yes", None))
    assert isinstance(result, NormalizedProp)
    assert result.market is PlayerMarket.GOALS
    assert result.line_type is LineType.MULTI_PLUS
    assert result.threshold == 1.0
    assert result.selection == "yes"


def test_goals_scored_over_alternate_line():
    result = normalize_prop_quote(_quote("player_goals_scored_over", "Over", 2.5))
    assert isinstance(result, NormalizedProp)
    assert result.market is PlayerMarket.GOALS
    assert result.line_type is LineType.OVER_UNDER
    assert result.threshold == 2.5
    assert result.selection == "over"


def test_unrecognised_market_key_is_unsupported_not_silently_dropped():
    result = normalize_prop_quote(_quote("player_marks_over", "Over", 6.5))
    assert isinstance(result, UnsupportedMarket)
    assert "player_marks_over" in result.reason


def test_unrecognised_selection_text_is_unsupported():
    result = normalize_prop_quote(_quote("player_disposals", "Push", 29.5))
    assert isinstance(result, UnsupportedMarket)


def test_over_under_with_no_threshold_is_unsupported_not_guessed():
    result = normalize_prop_quote(_quote("player_disposals", "Over", None))
    assert isinstance(result, UnsupportedMarket)
