from app.player_modelling.opportunity_sanity import filter_sane, sanity_check


def _player_opportunity(**overrides):
    o = {
        "opportunity_type": "player",
        "match_id": 100,
        "player_id": 1,
        "market_type": "player_disposals",
        "threshold": 20.5,
        "best_price": 2.0,
        "model_probability": 0.4,
        "selection_status": "confirmed_selected",
    }
    o.update(overrides)
    return o


def _team_opportunity(**overrides):
    o = {
        "opportunity_type": "team",
        "match_id": 100,
        "player_id": None,
        "market_type": "h2h",
        "threshold": None,
        "best_price": 1.9,
        "model_probability": 0.55,
        "selection_status": None,
        "quote_source": "the_odds_api",
    }
    o.update(overrides)
    return o


def test_valid_player_opportunity_passes():
    assert sanity_check(_player_opportunity()) is None


def test_valid_team_opportunity_passes():
    assert sanity_check(_team_opportunity()) is None


def test_rejects_odds_not_greater_than_one():
    assert sanity_check(_player_opportunity(best_price=1.0)) is not None
    assert sanity_check(_player_opportunity(best_price=0.9)) is not None


def test_rejects_invalid_probability():
    assert sanity_check(_player_opportunity(model_probability=0.0)) is not None
    assert sanity_check(_player_opportunity(model_probability=1.0)) is not None


def test_rejects_unresolved_match():
    assert sanity_check(_player_opportunity(match_id=None)) is not None


def test_rejects_unresolved_player_identity():
    assert sanity_check(_player_opportunity(player_id=None)) is not None


def test_rejects_confirmed_out_player():
    assert sanity_check(_player_opportunity(selection_status="confirmed_out")) is not None


def test_rejects_threshold_outside_modelled_disposal_range():
    assert sanity_check(_player_opportunity(threshold=2.0)) is not None
    assert sanity_check(_player_opportunity(threshold=60.0)) is not None


def test_rejects_threshold_outside_modelled_goal_range():
    assert sanity_check(_player_opportunity(market_type="player_goals", threshold=0.1)) is not None
    assert sanity_check(_player_opportunity(market_type="player_goals", threshold=15.0)) is not None


def test_accepts_threshold_within_modelled_goal_range():
    assert sanity_check(_player_opportunity(market_type="player_goals", threshold=2.0)) is None


def test_rejects_team_opportunity_backed_by_manual_quote():
    assert sanity_check(_team_opportunity(quote_source="manual")) is not None


def test_accepts_team_opportunity_backed_by_automated_quote():
    assert sanity_check(_team_opportunity(quote_source="the_odds_api")) is None


def test_filter_sane_splits_passed_and_rejected_with_reasons():
    good = _player_opportunity()
    bad = _player_opportunity(best_price=1.0)
    passed, rejected = filter_sane([good, bad])
    assert passed == [good]
    assert len(rejected) == 1
    assert rejected[0][0] == bad
    assert isinstance(rejected[0][1], str) and rejected[0][1]
