"""Regression tests for the Multi Builder selection refinement: market
relevance (a bookmaker-coverage PROXY), most-demanding-line-per-player,
fewest-legs preference, tier leg caps, honest joint-probability messaging,
and the post-settlement near-miss diagnostics (research context only)."""

from app.player_modelling.multi_builder import (
    MAX_LEGS, MIN_BOOKMAKERS_FOR_RELEVANCE, TIER_CONSERVATIVE, TIER_ORDER, TIER_RANGES, _combo_rank_key,
    _most_demanding_lines, _option_with_sgm_pricing, build_match_multis, market_relevance,
)
from app.player_modelling.multi_builder_diagnostics import LegResult, MultiResult, classify_multi, near_miss_summary

from tests.test_multi_builder import _add_player_leg, _all_options, _seed_match

BOOKS = ["SportsBet", "TAB", "Ladbrokes", "Neds"]


def _all_books(price):
    return [(b, price) for b in BOOKS]


def _seed_universe(db, match, home):
    """A positive-edge but far-below-any-floor line quoted by four bookmakers,
    so the match has a 4-bookmaker universe (relevance is only graded with
    at least MIN_BOOKMAKERS_FOR_RELEVANCE) without ever entering a pool."""
    assert len(BOOKS) >= MIN_BOOKMAKERS_FOR_RELEVANCE
    _add_player_leg(db, match, home, player_name="Universe Filler", threshold=30.5, predicted_mean=22.0, nb_alpha=0.3, prices=_all_books(15.0))


def _thresholds(result):
    return {leg["threshold"] for opt in _all_options(result) for leg in opt["legs"]}


# --- market relevance ------------------------------------------------------

def _synthetic_leg(p, share, *, quality=0.8):
    return {
        "opportunity_type": "player", "model_probability": p, "is_confirmed": True, "difference_pp": 5.0, "model_risk_flags": [],
        "opportunity_components": {"confidence": quality, "calibration": quality},
        "market_relevance": {"grade": "main" if share >= 0.5 else "thin", "coverage_share": share},
    }


def test_relevance_is_a_labelled_proxy_and_not_applied_to_tiny_universes():
    leg = {"opportunity_type": "player", "bookmakers": ["A", "B"], "n_bookmakers": 2}
    small = market_relevance(leg, 2)
    assert small["is_proxy"] is True and small["informative"] is False and small["grade"] == "main"
    graded = market_relevance(leg, 8)
    assert graded["informative"] is True and graded["grade"] == "thin" and graded["coverage_share"] == 0.25
    assert graded["bookmakers_offering"] == 2 and graded["bookmakers_in_match"] == 8


def test_relevance_breaks_ties_but_cannot_override_a_materially_stronger_weakest_leg():
    same_band_main = (_synthetic_leg(0.72, 1.0), _synthetic_leg(0.80, 1.0))
    same_band_thin = (_synthetic_leg(0.72, 0.25), _synthetic_leg(0.80, 0.25))
    assert _combo_rank_key(same_band_main, [], "high_probability") > _combo_rank_key(same_band_thin, [], "high_probability")

    # 0.90 vs 0.72 weakest leg is far more than the 5pp tie band: the thin
    # combination wins on probability, relevance never rescues the weaker one.
    strong_thin = (_synthetic_leg(0.90, 0.25), _synthetic_leg(0.92, 0.25))
    weak_main = (_synthetic_leg(0.72, 1.0), _synthetic_leg(0.80, 1.0))
    assert _combo_rank_key(strong_thin, [], "high_probability") > _combo_rank_key(weak_main, [], "high_probability")


def test_fewer_legs_beats_higher_weakest_leg_probability():
    two = (_synthetic_leg(0.66, 1.0), _synthetic_leg(0.70, 1.0))
    three = (_synthetic_leg(0.90, 1.0), _synthetic_leg(0.92, 1.0), _synthetic_leg(0.93, 1.0))
    assert _combo_rank_key(two, [], "high_probability") > _combo_rank_key(three, [], "high_probability")


def test_most_demanding_lines_keeps_the_hardest_qualifying_lines_only():
    def leg(threshold, price):
        return {
            "opportunity_type": "player", "_family_key": ("p1", "disposals"), "threshold": threshold, "line_type": "over_under",
            "bookmaker_price": price,
        }
    kept = _most_demanding_lines([leg(9.5, 1.05), leg(14.5, 1.3), leg(19.5, 1.7)])
    assert sorted(l["threshold"] for l in kept) == [14.5, 19.5]


def test_most_demanding_lines_never_bans_a_low_line_that_is_the_only_one_qualifying():
    legs = [{
        "opportunity_type": "player", "_family_key": ("p1", "disposals"), "threshold": 9.5, "line_type": "over_under", "bookmaker_price": 1.2,
    }]
    assert _most_demanding_lines(legs) == legs


# --- selection on real builder output --------------------------------------

def test_obscure_low_threshold_line_does_not_beat_mainstream_line(db_session):
    """The 5.5 line is quoted by one bookmaker and is priced very short; the
    15.5 line is offered by all four. Only the mainstream line may be used."""
    match, home, away = _seed_match(db_session)
    _seed_universe(db_session, match, home)
    for i in range(4):
        _add_player_leg(db_session, match, home, player_name=f"Player {i}", threshold=5.5, predicted_mean=26.0, nb_alpha=0.3, prices=[("SportsBet", 1.05)])
        _add_player_leg(db_session, match, home, player_name=f"Player {i}", threshold=15.5, predicted_mean=26.0, nb_alpha=0.3, prices=_all_books(1.45))

    for main_only in (True, False):
        result = build_match_multis(db_session, match.id, confirmed_only=True, main_markets_only=main_only)
        options = _all_options(result)
        assert options
        assert _thresholds(result) == {15.5}, f"main_markets_only={main_only}"
        assert not any(opt["includes_less_common_lines"] for opt in options)


def test_thin_lines_only_used_when_allowed_and_then_flagged(db_session):
    match, home, away = _seed_match(db_session)
    _seed_universe(db_session, match, home)
    for i in range(3):
        _add_player_leg(db_session, match, home, player_name=f"Thin Player {i}", threshold=10.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.45)])

    strict = build_match_multis(db_session, match.id, confirmed_only=True, main_markets_only=True)
    assert not _all_options(strict)
    reason = strict.tiers[TIER_CONSERVATIVE].unavailable_reason
    assert "Main markets only" in reason

    relaxed = build_match_multis(db_session, match.id, confirmed_only=True, main_markets_only=False)
    options = _all_options(relaxed)
    assert options and all(opt["includes_less_common_lines"] for opt in options)
    assert relaxed.main_markets_only is False


def test_prefers_two_legs_over_three_when_both_reach_the_band(db_session):
    match, home, away = _seed_match(db_session)
    for i in range(2):
        _add_player_leg(db_session, match, home, player_name=f"Long Price {i}", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.45)])
    for i in range(3):
        _add_player_leg(db_session, match, home, player_name=f"Short Price {i}", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.25)])
    # 1.45*1.45=2.10 (2 legs) vs 1.25^3=1.95 (3 legs, safer weakest leg).
    result = build_match_multis(db_session, match.id, confirmed_only=True)
    first = result.tiers[TIER_CONSERVATIVE].options[0]
    assert first["n_legs"] == 2


def test_every_option_respects_tier_odds_range_leg_cap_and_single_bookmaker(db_session):
    match, home, away = _seed_match(db_session)
    _seed_universe(db_session, match, home)
    for i in range(8):
        team = home if i % 2 == 0 else away
        for threshold, price in ((10.5, 1.15), (15.5, 1.45), (20.5, 2.2)):
            _add_player_leg(db_session, match, team, player_name=f"Range Player {i}", threshold=threshold, predicted_mean=24.0, nb_alpha=0.25, prices=_all_books(price))
    result = build_match_multis(db_session, match.id, confirmed_only=True)
    options = _all_options(result)
    assert options
    for tier in TIER_ORDER:
        lo, hi = TIER_RANGES[tier]
        for opt in result.tiers[tier].options:
            assert opt["indicative_combined_odds"] >= lo
            assert hi is None or opt["indicative_combined_odds"] <= hi
            assert opt["n_legs"] <= MAX_LEGS[tier]
            assert opt["bookmaker"] in BOOKS
            player_ids = [leg["player_id"] for leg in opt["legs"]]
            assert len(player_ids) == len(set(player_ids)), "same player twice in one multi"


def test_confirmed_combination_preferred_over_provisional(db_session):
    match, home, away = _seed_match(db_session)
    for i in range(3):
        _add_player_leg(db_session, match, home, player_name=f"Confirmed {i}", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.30)])
    _add_player_leg(db_session, match, home, player_name="Unconfirmed", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.30)], confirmed=False)

    result = build_match_multis(db_session, match.id, confirmed_only=False)
    first = result.tiers[TIER_CONSERVATIVE].options[0]
    assert first["provisional"] is False


# --- honest joint-probability messaging ------------------------------------

def test_option_reports_price_implied_probability_and_never_a_model_joint_probability(db_session):
    match, home, away = _seed_match(db_session)
    for i in range(3):
        _add_player_leg(db_session, match, home, player_name=f"Safe Player {i}", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.30)])
    result = build_match_multis(db_session, match.id, confirmed_only=True)
    option = result.tiers[TIER_CONSERVATIVE].options[0]
    d = _option_with_sgm_pricing(db_session, match.id, option)
    assert abs(d["price_implied_probability"] - 1 / d["indicative_combined_odds"]) < 1e-9
    assert d["joint_probability_note"]
    assert not any("joint_probability" == k or "combined_probability" in k for k in d)
    assert d["selection_note"]
    for leg in d["legs"]:
        assert leg["market_relevance"]["is_proxy"] is True


# --- near-miss diagnostics (research only) ---------------------------------

def _leg(outcome, *, threshold=24.5, actual=None, market="player_disposals", line_type="over_under"):
    return LegResult(label="L", market_type=market, outcome=outcome, threshold=threshold, actual_value=actual, line_type=line_type)


def test_near_miss_summary_reports_hits_and_shortfall_without_reclassifying():
    multi = MultiResult(
        source="placed", multi_id="m1",
        legs=(_leg("won", actual=30), _leg("won", actual=27), _leg("won", actual=26), _leg("won", actual=25), _leg("lost", actual=23)),
    )
    summary = near_miss_summary(multi)
    assert summary["headline"] == "4 of 5 legs hit"
    assert summary["pattern"] == "one_miss"
    assert summary["closest_miss"] == 2.0  # 24.5+ needs 25; finished on 23
    assert classify_multi(multi).n_lost == 1  # still a loss


def test_near_miss_summary_goals_and_multiple_misses():
    multi = MultiResult(
        source="placed", multi_id="m2",
        legs=(
            _leg("lost", threshold=2.0, actual=1, market="player_goals", line_type="multi_plus"),
            _leg("lost", actual=20), _leg("won", actual=31),
        ),
    )
    summary = near_miss_summary(multi)
    assert summary["pattern"] == "two_miss"
    assert summary["closest_miss"] == 1.0
    assert {f["shortfall"] for f in summary["failed_legs"]} == {1.0, 5.0}


def test_near_miss_summary_is_none_until_decided():
    pending = MultiResult(source="placed", multi_id="m3", legs=(_leg("won", actual=30), _leg("pending")))
    assert near_miss_summary(pending) is None
