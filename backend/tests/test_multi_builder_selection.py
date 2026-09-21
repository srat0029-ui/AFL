"""Regression tests for the Multi Builder selection refinement: market
relevance (a bookmaker-coverage PROXY), most-demanding-line-per-player,
fewest-legs preference, tier leg caps, honest joint-probability messaging,
and the post-settlement near-miss diagnostics (research context only)."""

from app.player_modelling.multi_builder import (
    MAX_LEGS, MIN_BOOKMAKERS_FOR_RELEVANCE, TIER_CONSERVATIVE, TIER_ORDER, TIER_RANGES, _all_alternate_legs,
    _calibration_checked_at_threshold, _candidate_pool, _combo_rank_key, _high_probability_score, _most_demanding_lines,
    _option_with_sgm_pricing, _ranking_calibration, _ranking_opportunity_score, _reasons_for, _warnings_for,
    bookmaker_universe, build_match_multis, market_relevance,
)
from app.models.bookmaker import ELIGIBILITY_EXCLUDED, ELIGIBILITY_INCLUDED
from app.player_modelling.multi_builder_diagnostics import LegResult, MultiResult, classify_multi, near_miss_summary

from tests.test_multi_builder import _add_player_leg, _all_options, _bookmaker, _seed_match

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


def _entries(*names, eligibility=ELIGIBILITY_INCLUDED):
    return [{"bookmaker_name": n, "eligibility": eligibility} for n in names]


def test_relevance_is_a_labelled_proxy_and_not_applied_to_tiny_universes():
    leg = {"opportunity_type": "player", "bookmakers": _entries("A", "B"), "n_bookmakers": 2}
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


# --- exact-threshold calibration in RANKING ---------------------------------

def _cal_leg(threshold, evaluated, *, p=0.7, cal_component=5.0, line_type="over_under"):
    return {
        "opportunity_type": "player", "market_type": "player_disposals", "model_probability": p, "is_confirmed": True,
        "difference_pp": 0.05, "model_risk_flags": [], "warnings": [], "threshold": threshold, "line_type": line_type,
        "opportunity_score": 60.0, "best_price": 1.5,
        "opportunity_components": {"confidence": 10.0, "calibration": cal_component, "freshness": 5.0, "penalty_multiplier": 1.0},
        "calibration": None if evaluated is None else {"evaluated_threshold": float(evaluated), "ece": 0.02, "n": 5000},
    }


def test_borrowed_calibration_gives_no_ranking_benefit():
    """A 15+ line carries the 20+ evaluation (nearest threshold). It must rank
    exactly like a leg with no calibration data, not like a calibrated one."""
    borrowed = _cal_leg(14.5, 20)
    none = _cal_leg(14.5, None, cal_component=0.0)
    assert not _calibration_checked_at_threshold(borrowed)
    assert _ranking_calibration(borrowed) == 0.0
    assert _high_probability_score(borrowed) == _high_probability_score(none)
    assert _combo_rank_key((borrowed, borrowed), [], "high_probability") == _combo_rank_key((none, none), [], "high_probability")
    # Value mode: the 5 borrowed points are removed from the score.
    assert _ranking_opportunity_score(borrowed) == _ranking_opportunity_score({**none, "opportunity_score": 55.0})


def test_borrowed_calibration_does_not_beat_a_leg_with_no_calibration_data():
    borrowed = _cal_leg(14.5, 20)
    none = _cal_leg(14.5, None, cal_component=0.0)
    assert _combo_rank_key((borrowed, borrowed), [], "high_probability") <= _combo_rank_key((none, none), [], "high_probability")


def test_exact_threshold_calibration_still_contributes_at_20_25_30_35():
    for evaluated in (20, 25, 30, 35):
        leg = _cal_leg(evaluated - 0.5, evaluated)  # "20+" is the 19.5 line
        none = _cal_leg(evaluated - 0.5, None, cal_component=0.0)
        assert _calibration_checked_at_threshold(leg), evaluated
        assert _ranking_calibration(leg) == 5.0
        assert _high_probability_score(leg) > _high_probability_score(none)
        assert _combo_rank_key((leg, leg), [], "high_probability") > _combo_rank_key((none, none), [], "high_probability")
        assert _ranking_opportunity_score(leg) == _ranking_opportunity_score({**none, "opportunity_score": 60.0})


def test_exact_goal_calibration_matches_both_line_shapes():
    assert _calibration_checked_at_threshold({**_cal_leg(1.0, 1, line_type="multi_plus"), "market_type": "player_goals"})
    assert _calibration_checked_at_threshold({**_cal_leg(0.5, 1), "market_type": "player_goals"})  # over 0.5 == 1+
    assert not _calibration_checked_at_threshold({**_cal_leg(2.0, 1, line_type="multi_plus"), "market_type": "player_goals"})


def test_missing_exact_calibration_does_not_exclude_an_otherwise_valid_leg():
    borrowed = {**_cal_leg(14.5, 20, p=0.8), "_family_key": ("p1", "disposals"), "bookmaker_price": 1.4}
    pool = _candidate_pool([borrowed], TIER_CONSERVATIVE, "high_probability", confirmed_only=True)
    assert pool == [borrowed]
    # the existing explanatory warning is kept, and no calibrated-at-threshold reason is claimed
    borrowed_full = {
        **borrowed, "quality_tier": {"tier": "ok"}, "odds_freshness": "fresh", "confidence_tier": "higher_confidence", "n_bookmakers": 4,
        "bookmakers": _entries("A", "B", "C", "D"),
    }
    assert any(w["code"] == "CALIBRATION_NOT_AT_THRESHOLD" for w in _warnings_for(borrowed_full))
    assert not any(r["code"] == "WELL_CALIBRATED_THRESHOLD" for r in _reasons_for(borrowed_full))


def test_builder_still_offers_multis_when_no_leg_has_exact_calibration(db_session):
    """Fixture legs have no promoted calibration metrics at all: every leg is
    without exact-threshold calibration, and multis must still be produced."""
    match, home, away = _seed_match(db_session)
    for i in range(3):
        _add_player_leg(db_session, match, home, player_name=f"Uncalibrated {i}", threshold=5.5, predicted_mean=22.0, nb_alpha=0.3, prices=[("SportsBet", 1.30)])
    result = build_match_multis(db_session, match.id, confirmed_only=True)
    assert result.tiers[TIER_CONSERVATIVE].options


# --- market relevance uses the ELIGIBLE bookmaker universe -------------------

def _exclude(db, *names):
    for name in names:
        b = _bookmaker(db, name)
        b.eligibility = ELIGIBILITY_EXCLUDED
    db.commit()


def test_relevance_counts_only_eligible_bookmakers(db_session):
    match, home, away = _seed_match(db_session)
    excluded = ["Exchange A", "Exchange B", "Exchange C", "Exchange D"]
    _exclude(db_session, *excluded)
    # Universe: 4 eligible bookmakers (SportsBet/TAB/Ladbrokes/Neds) + 4 excluded providers.
    _add_player_leg(db_session, match, home, player_name="Filler", threshold=30.5, predicted_mean=22.0, nb_alpha=0.3, prices=_all_books(15.0) + [(b, 15.0) for b in excluded])
    # Widely quoted by excluded providers but by ONE eligible bookmaker: naively 5/8 "main", eligibly 1/4 "thin".
    _add_player_leg(db_session, match, home, player_name="Excluded Heavy", threshold=10.5, predicted_mean=22.0, nb_alpha=0.3,
                    prices=[("SportsBet", 1.3)] + [(b, 1.3) for b in excluded])
    # All four eligible bookmakers plus two excluded ones: 4/4, never above 100%.
    _add_player_leg(db_session, match, home, player_name="Fully Offered", threshold=10.5, predicted_mean=22.0, nb_alpha=0.3,
                    prices=_all_books(1.3) + [(b, 1.3) for b in excluded[:2]])

    legs = {leg["player_name"]: leg for leg in _all_alternate_legs(db_session, match.id)}
    assert bookmaker_universe(list(legs.values())) == 4
    heavy = legs["Excluded Heavy"]["market_relevance"]
    assert heavy["bookmakers_offering"] == 1 and heavy["bookmakers_in_match"] == 4
    assert heavy["coverage_share"] == 0.25 and heavy["grade"] == "thin"
    full = legs["Fully Offered"]["market_relevance"]
    assert full["bookmakers_offering"] == 4 and full["coverage_share"] == 1.0 and full["grade"] == "main"


def test_excluded_providers_alone_do_not_make_the_relevance_universe_informative(db_session):
    match, home, away = _seed_match(db_session)
    excluded = ["Exchange A", "Exchange B", "Exchange C", "Exchange D"]
    _exclude(db_session, *excluded)
    _add_player_leg(db_session, match, home, player_name="Only Two Eligible", threshold=10.5, predicted_mean=22.0, nb_alpha=0.3,
                    prices=[("SportsBet", 1.3), ("TAB", 1.3)] + [(b, 1.3) for b in excluded])
    leg = _all_alternate_legs(db_session, match.id)[0]
    rel = leg["market_relevance"]
    assert rel["bookmakers_in_match"] == 2 and rel["informative"] is False  # 6 quoted, only 2 usable


def test_relevance_label_says_eligible_bookmakers():
    leg = {
        "opportunity_type": "player", "market_type": "player_disposals", "model_probability": 0.7, "is_confirmed": True, "difference_pp": 0.05,
        "quality_tier": {"tier": "ok"}, "odds_freshness": "fresh", "confidence_tier": "higher_confidence", "threshold": 14.5,
        "line_type": "over_under", "calibration": None, "bookmakers": _entries("A", "B", "C", "D", "E"),
        "market_relevance": {"grade": "main", "coverage_share": 5 / 7, "bookmakers_offering": 5, "bookmakers_in_match": 7, "informative": True},
    }
    labels = [r["label"] for r in _reasons_for(leg) if r["code"] == "MARKET_COVERAGE"]
    assert labels == ["Offered by 5 of 7 eligible bookmakers"]
