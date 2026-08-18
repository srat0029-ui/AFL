"""Tests for structured uncertainty flags (Weekly Bet Review stage,
Section 11)."""

from app.player_modelling.uncertainty_flags import (
    FLAG_EARLY_MARKET,
    FLAG_ELITE_PLAYER_CONSERVATIVE_MODEL,
    FLAG_LARGE_BOOKMAKER_DISAGREEMENT,
    FLAG_MODEL_WEAKER_IN_REGION,
    FLAG_RARE_EVENT_THRESHOLD,
    FLAG_RECENT_FORM_DISAGREES,
    FLAG_SINGLE_BOOK_MARKET,
    FLAG_TEAMS_NOT_CONFIRMED,
    UncertaintyFlagInputs,
    compute_uncertainty_flags,
)


class _FakeBand:
    def __init__(self, avg_predicted, actual_rate, meets_min_sample=True):
        self.avg_predicted = avg_predicted
        self.actual_rate = actual_rate
        self.meets_min_sample = meets_min_sample


class _FakeOutlier:
    def __init__(self, is_outlier):
        self.is_outlier = is_outlier


def _base_opportunity(**overrides):
    o = {
        "opportunity_type": "team",
        "n_bookmakers": 5,
        "market_maturity": {"tier": "mature_market"},
        "market_type": "h2h",
        "warnings": [],
        "recent_form": None,
    }
    o.update(overrides)
    return o


def test_teams_not_confirmed_flag_only_for_team_markets_without_confirmed_lineups():
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=False)
    flags = compute_uncertainty_flags(_base_opportunity(opportunity_type="team"), inputs)
    assert FLAG_TEAMS_NOT_CONFIRMED in flags


def test_teams_not_confirmed_flag_absent_when_lineups_confirmed():
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True)
    flags = compute_uncertainty_flags(_base_opportunity(opportunity_type="team"), inputs)
    assert FLAG_TEAMS_NOT_CONFIRMED not in flags


def test_single_book_market_flag():
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True)
    flags = compute_uncertainty_flags(_base_opportunity(n_bookmakers=1), inputs)
    assert FLAG_SINGLE_BOOK_MARKET in flags


def test_early_market_flag():
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True)
    flags = compute_uncertainty_flags(_base_opportunity(market_maturity={"tier": "early_market"}), inputs)
    assert FLAG_EARLY_MARKET in flags


def test_large_bookmaker_disagreement_from_outlier_check():
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True, outlier_check=_FakeOutlier(True))
    flags = compute_uncertainty_flags(_base_opportunity(), inputs)
    assert FLAG_LARGE_BOOKMAKER_DISAGREEMENT in flags


def test_large_bookmaker_disagreement_from_wide_consensus_spread():
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True, outlier_check=_FakeOutlier(False), consensus_spread=0.10)
    flags = compute_uncertainty_flags(_base_opportunity(), inputs)
    assert FLAG_LARGE_BOOKMAKER_DISAGREEMENT in flags


def test_no_bookmaker_disagreement_flag_when_tight():
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True, outlier_check=_FakeOutlier(False), consensus_spread=0.01)
    flags = compute_uncertainty_flags(_base_opportunity(), inputs)
    assert FLAG_LARGE_BOOKMAKER_DISAGREEMENT not in flags


def test_model_weaker_in_region_when_calibration_gap_large():
    band = _FakeBand(avg_predicted=0.60, actual_rate=0.40, meets_min_sample=True)
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True, calibration_band=band)
    flags = compute_uncertainty_flags(_base_opportunity(), inputs)
    assert FLAG_MODEL_WEAKER_IN_REGION in flags


def test_model_weaker_flag_absent_when_sample_too_small():
    band = _FakeBand(avg_predicted=0.60, actual_rate=0.40, meets_min_sample=False)
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True, calibration_band=band)
    flags = compute_uncertainty_flags(_base_opportunity(), inputs)
    assert FLAG_MODEL_WEAKER_IN_REGION not in flags


def test_elite_player_conservative_model_flag_only_for_disposals():
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True, elite_disposal_bucket="elite_28_plus")
    flags = compute_uncertainty_flags(_base_opportunity(opportunity_type="player", market_type="player_disposals"), inputs)
    assert FLAG_ELITE_PLAYER_CONSERVATIVE_MODEL in flags

    flags_goals = compute_uncertainty_flags(_base_opportunity(opportunity_type="player", market_type="player_goals"), inputs)
    assert FLAG_ELITE_PLAYER_CONSERVATIVE_MODEL not in flags_goals


def test_rare_event_threshold_flag_from_warnings():
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True)
    flags = compute_uncertainty_flags(_base_opportunity(warnings=["Rare-event threshold — smaller sample."]), inputs)
    assert FLAG_RARE_EVENT_THRESHOLD in flags


def test_recent_form_disagrees_flag():
    inputs = UncertaintyFlagInputs(any_confirmed_player_lineups=True)
    flags = compute_uncertainty_flags(_base_opportunity(recent_form={"form_disagreement_label": "Model much higher than recent hit rate"}), inputs)
    assert FLAG_RECENT_FORM_DISAGREES in flags


def test_never_hides_flags_regardless_of_how_many_fire():
    band = _FakeBand(avg_predicted=0.60, actual_rate=0.40)
    inputs = UncertaintyFlagInputs(
        any_confirmed_player_lineups=False, calibration_band=band, outlier_check=_FakeOutlier(True),
        elite_disposal_bucket="elite_28_plus", tog_volatile=True,
    )
    flags = compute_uncertainty_flags(
        _base_opportunity(opportunity_type="player", market_type="player_disposals", n_bookmakers=1, market_maturity={"tier": "early_market"}, warnings=["Rare-event"], recent_form={"form_disagreement_label": "x"}),
        inputs,
    )
    # Every applicable flag should fire simultaneously - none suppressed for looking "too negative".
    assert len(flags) >= 6
