"""Tests for the Weekly Review round summary (Weekly Bet Review stage,
Section 17) — hypothetical flat-$1 outcomes, small-sample warnings, never
retunes anything."""

from datetime import datetime, timezone

from app.models import WeeklyShortlistSnapshot, WeeklyShortlistSnapshotItem
from app.player_modelling.round_summary import SMALL_SAMPLE_THRESHOLD, build_round_summary

NOW = datetime.now(timezone.utc)


def _make_item(*, match_id=1, opportunity_type="team", best_price=2.0, match_result=None, confidence_tier="higher_confidence", quality_tier="strong_candidate"):
    return WeeklyShortlistSnapshotItem(
        rank=1, opportunity_type=opportunity_type, label="Test Opportunity", match_id=match_id, market_type="h2h",
        selection="Team A", best_price=best_price, best_bookmaker="TAB", recorded_at=NOW,
        model_probability=0.6, model_fair_odds=1.67, market_implied_probability=0.5, overround_removed=False,
        difference_pp=0.1, expected_value=0.2, confidence_tier=confidence_tier, quality_tier=quality_tier, n_bookmakers=3,
        reasons_json={}, match_result=match_result, settled_at=NOW if match_result else None,
    )


def test_won_item_flat_stake_pl_is_price_minus_one():
    snapshot = WeeklyShortlistSnapshot(round_number=1, n_items=1, include_unconfirmed_players=False)
    snapshot.items = [_make_item(best_price=2.5, match_result="won")]
    summary = build_round_summary(snapshot)
    assert summary.items[0].flat_stake_pl == 1.5
    assert summary.hypothetical_flat_stake_pl == 1.5


def test_lost_item_flat_stake_pl_is_negative_one():
    snapshot = WeeklyShortlistSnapshot(round_number=1, n_items=1, include_unconfirmed_players=False)
    snapshot.items = [_make_item(best_price=2.5, match_result="lost")]
    summary = build_round_summary(snapshot)
    assert summary.items[0].flat_stake_pl == -1.0


def test_push_item_flat_stake_pl_is_zero():
    snapshot = WeeklyShortlistSnapshot(round_number=1, n_items=1, include_unconfirmed_players=False)
    snapshot.items = [_make_item(match_result="push")]
    summary = build_round_summary(snapshot)
    assert summary.items[0].flat_stake_pl == 0.0


def test_unresolved_item_flat_stake_pl_is_none():
    snapshot = WeeklyShortlistSnapshot(round_number=1, n_items=1, include_unconfirmed_players=False)
    snapshot.items = [_make_item(match_result=None)]
    summary = build_round_summary(snapshot)
    assert summary.items[0].flat_stake_pl is None
    assert summary.n_unresolved == 1


def test_hypothetical_pl_sums_only_settled_items():
    snapshot = WeeklyShortlistSnapshot(round_number=1, n_items=3, include_unconfirmed_players=False)
    snapshot.items = [
        _make_item(match_id=1, best_price=3.0, match_result="won"),
        _make_item(match_id=2, best_price=2.0, match_result="lost"),
        _make_item(match_id=3, match_result=None),
    ]
    summary = build_round_summary(snapshot)
    assert summary.hypothetical_flat_stake_pl == 2.0 + (-1.0)
    assert summary.n_settled == 2
    assert summary.n_unresolved == 1


def test_small_sample_warning_true_below_threshold():
    snapshot = WeeklyShortlistSnapshot(round_number=1, n_items=1, include_unconfirmed_players=False)
    snapshot.items = [_make_item(match_result="won")]
    summary = build_round_summary(snapshot)
    assert summary.n_settled < SMALL_SAMPLE_THRESHOLD
    assert summary.small_sample_warning is True


def test_team_vs_player_breakdown():
    snapshot = WeeklyShortlistSnapshot(round_number=1, n_items=2, include_unconfirmed_players=False)
    snapshot.items = [_make_item(opportunity_type="team"), _make_item(match_id=2, opportunity_type="player")]
    summary = build_round_summary(snapshot)
    assert summary.n_team == 1
    assert summary.n_player == 1


def test_confidence_and_quality_tier_breakdowns():
    snapshot = WeeklyShortlistSnapshot(round_number=1, n_items=2, include_unconfirmed_players=False)
    snapshot.items = [
        _make_item(confidence_tier="higher_confidence", quality_tier="strong_candidate"),
        _make_item(match_id=2, confidence_tier="moderate_confidence", quality_tier="worth_reviewing"),
    ]
    summary = build_round_summary(snapshot)
    assert summary.confidence_tier_breakdown == {"higher_confidence": 1, "moderate_confidence": 1}
    assert summary.quality_tier_breakdown == {"strong_candidate": 1, "worth_reviewing": 1}


def test_unique_matches_counted_correctly():
    snapshot = WeeklyShortlistSnapshot(round_number=1, n_items=2, include_unconfirmed_players=False)
    snapshot.items = [_make_item(match_id=1), _make_item(match_id=1)]  # same match, two markets
    summary = build_round_summary(snapshot)
    assert summary.n_unique_matches == 1
