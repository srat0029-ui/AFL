from app.player_modelling.opportunity_reason_codes import (
    REASON_BEST_PRICE_CLEARLY_HIGHER,
    REASON_CONFIRMED_LINEUP,
    REASON_HIGHER_CONFIDENCE,
    REASON_MODEL_ABOVE_MARKET,
    REASON_RARE_EVENT_THRESHOLD,
    REASON_RECENT_FORM_DISAGREES,
    REASON_SINGLE_BOOK_ONLY,
    REASON_WELL_CALIBRATED,
    compute_reason_codes,
    reason_labels,
)


def _base_opportunity(**overrides):
    o = {
        "difference_pp": 0.05,
        "bookmakers": [{"bookmaker_name": "SportsBet", "price_decimal": 2.0}],
        "confidence_tier": "moderate_confidence",
        "calibration": None,
        "is_confirmed": False,
        "warnings": [],
    }
    o.update(overrides)
    return o


def test_model_above_market_when_difference_positive():
    codes = compute_reason_codes(_base_opportunity(difference_pp=0.05))
    assert REASON_MODEL_ABOVE_MARKET in codes


def test_model_above_market_absent_when_difference_non_positive():
    codes = compute_reason_codes(_base_opportunity(difference_pp=-0.01))
    assert REASON_MODEL_ABOVE_MARKET not in codes


def test_single_book_only_flagged_with_one_bookmaker():
    codes = compute_reason_codes(_base_opportunity())
    assert REASON_SINGLE_BOOK_ONLY in codes


def test_best_price_clearly_higher_when_meaningfully_ahead():
    codes = compute_reason_codes(
        _base_opportunity(
            bookmakers=[
                {"bookmaker_name": "TAB", "price_decimal": 2.20},
                {"bookmaker_name": "SportsBet", "price_decimal": 1.90},
            ]
        )
    )
    assert REASON_BEST_PRICE_CLEARLY_HIGHER in codes
    assert REASON_SINGLE_BOOK_ONLY not in codes


def test_best_price_not_clearly_higher_when_books_agree():
    codes = compute_reason_codes(
        _base_opportunity(
            bookmakers=[
                {"bookmaker_name": "TAB", "price_decimal": 2.00},
                {"bookmaker_name": "SportsBet", "price_decimal": 1.98},
            ]
        )
    )
    assert REASON_BEST_PRICE_CLEARLY_HIGHER not in codes


def test_higher_confidence_reason():
    codes = compute_reason_codes(_base_opportunity(confidence_tier="higher_confidence"))
    assert REASON_HIGHER_CONFIDENCE in codes


def test_well_calibrated_reason_from_low_ece():
    codes = compute_reason_codes(_base_opportunity(calibration={"ece": 0.01, "evaluated_threshold": 20, "n": 1000}))
    assert REASON_WELL_CALIBRATED in codes


def test_well_calibrated_absent_when_ece_high():
    codes = compute_reason_codes(_base_opportunity(calibration={"ece": 0.08, "evaluated_threshold": 20, "n": 1000}))
    assert REASON_WELL_CALIBRATED not in codes


def test_confirmed_lineup_reason():
    codes = compute_reason_codes(_base_opportunity(is_confirmed=True))
    assert REASON_CONFIRMED_LINEUP in codes


def test_rare_event_threshold_from_warning_text():
    codes = compute_reason_codes(_base_opportunity(warnings=["Rare-event probability — treat with caution."]))
    assert REASON_RARE_EVENT_THRESHOLD in codes


def test_recent_form_disagrees_passed_through():
    codes = compute_reason_codes(_base_opportunity(), form_disagreement=True)
    assert REASON_RECENT_FORM_DISAGREES in codes


def test_reason_labels_are_human_readable_and_match_codes():
    codes = [REASON_MODEL_ABOVE_MARKET, REASON_SINGLE_BOOK_ONLY]
    labels = reason_labels(codes)
    assert len(labels) == 2
    assert all(isinstance(label, str) and label for label in labels)
