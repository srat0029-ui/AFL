"""Tests for the read-only Placed Bets analytics module: overall hit rate,
flat-$1 ROI, and every requested split, plus the small-sample "exploratory"
label. Pure computation over PlacedBet objects - no DB, no settlement, no
model/ranking code touched."""

from app.models import PlacedBet
from app.player_modelling.placed_bet_analytics import MIN_SAMPLE_FOR_LABELED, compute_placed_bet_analytics


def _bet(status="pending", odds=2.0, market_type="player_disposals", source_mode="high_probability",
         probability=0.65, confidence_tier="higher_confidence") -> PlacedBet:
    return PlacedBet(
        match_id=1, opportunity_type="player", label="x", selection="over", market_type=market_type,
        line_type="over_under", threshold=20.5, bookmaker="TAB", odds_taken=odds, source_mode=source_mode,
        model_probability=probability, model_fair_odds=1 / probability, confidence_tier=confidence_tier,
        status=status,
    )


def test_only_settled_bets_are_counted():
    bets = [_bet(status="pending"), _bet(status="pending"), _bet(status="won")]
    result = compute_placed_bet_analytics(bets)
    assert result.n_total_settled == 1


def test_hit_rate_excludes_voids_from_denominator():
    bets = [_bet(status="won"), _bet(status="lost"), _bet(status="void")]
    result = compute_placed_bet_analytics(bets)
    assert result.n_total_settled == 3
    assert result.wins == 1
    assert result.losses == 1
    assert result.voids == 1
    assert result.hit_rate == 0.5  # 1 win / (1 win + 1 loss), void excluded


def test_hit_rate_is_none_with_no_decided_bets():
    result = compute_placed_bet_analytics([_bet(status="void")])
    assert result.hit_rate is None
    assert result.avg_odds_taken is None
    assert result.flat_stake_units is None


def test_flat_stake_units_and_roi():
    bets = [_bet(status="won", odds=2.0), _bet(status="lost", odds=1.5)]
    result = compute_placed_bet_analytics(bets)
    # +1.0 (won at 2.0 -> profit 1.0) + -1.0 (lost) = 0.0 net units
    assert result.flat_stake_units == 0.0
    assert result.flat_stake_roi_pct == 0.0
    assert result.avg_odds_taken == 1.75


def test_exploratory_flag_below_threshold():
    bets = [_bet(status="won") for _ in range(MIN_SAMPLE_FOR_LABELED - 1)]
    result = compute_placed_bet_analytics(bets)
    assert result.exploratory is True

    bets_enough = [_bet(status="won") for _ in range(MIN_SAMPLE_FOR_LABELED)]
    result2 = compute_placed_bet_analytics(bets_enough)
    assert result2.exploratory is False


def test_split_by_source_mode_in_fixed_order_omits_empty_groups():
    bets = [_bet(status="won", source_mode="manual"), _bet(status="lost", source_mode="high_probability")]
    result = compute_placed_bet_analytics(bets)
    labels = [s.label for s in result.by_source_mode]
    assert labels == ["high_probability", "manual"]  # fixed order, not insertion order
    hp = next(s for s in result.by_source_mode if s.label == "high_probability")
    assert hp.n_settled == 1 and hp.losses == 1


def test_split_by_market_type():
    bets = [_bet(status="won", market_type="h2h"), _bet(status="won", market_type="player_goals")]
    result = compute_placed_bet_analytics(bets)
    labels = [s.label for s in result.by_market_type]
    assert labels == ["player_goals", "h2h"]  # fixed MARKET_TYPE_ORDER


def test_split_by_probability_bucket():
    bets = [_bet(status="won", probability=0.45), _bet(status="won", probability=0.55), _bet(status="won", probability=0.85)]
    result = compute_placed_bet_analytics(bets)
    labels = {s.label: s.n_settled for s in result.by_probability_bucket}
    assert labels == {"Under 50%": 1, "50-60%": 1, "80%+": 1}


def test_split_by_confidence_tier():
    bets = [_bet(status="won", confidence_tier="lower_confidence"), _bet(status="lost", confidence_tier="lower_confidence")]
    result = compute_placed_bet_analytics(bets)
    assert len(result.by_confidence_tier) == 1
    assert result.by_confidence_tier[0].label == "lower_confidence"
    assert result.by_confidence_tier[0].hit_rate == 0.5


def test_each_split_reports_its_own_exploratory_flag_independently():
    bets = [_bet(status="won", source_mode="manual") for _ in range(MIN_SAMPLE_FOR_LABELED)]
    bets += [_bet(status="won", source_mode="best_value")]  # only 1 bet in this split
    result = compute_placed_bet_analytics(bets)
    by_label = {s.label: s for s in result.by_source_mode}
    assert by_label["manual"].exploratory is False
    assert by_label["best_value"].exploratory is True
