"""Tests for the descriptive-only market maturity indicator (Market
Integrity stage, Section 12)."""

from datetime import datetime, timedelta, timezone

from app.player_modelling.market_maturity import (
    DEVELOPING_MARKET,
    EARLY_MARKET,
    MATURE_MARKET,
    classify_market_maturity,
    hours_until,
)


def test_early_market_with_few_bookmakers_and_no_snapshots():
    m = classify_market_maturity(n_bookmakers=1, hours_until_kickoff=100.0, snapshot_count=None)
    assert m.tier == EARLY_MARKET


def test_mature_market_with_many_bookmakers_snapshots_and_close_to_kickoff():
    m = classify_market_maturity(n_bookmakers=8, hours_until_kickoff=10.0, snapshot_count=5)
    assert m.tier == MATURE_MARKET


def test_developing_market_with_moderate_breadth():
    m = classify_market_maturity(n_bookmakers=4, hours_until_kickoff=200.0, snapshot_count=None)
    assert m.tier == DEVELOPING_MARKET


def test_hours_until_handles_naive_datetimes_as_utc():
    now = datetime(2026, 8, 18, 0, 0, 0)  # naive
    kickoff = datetime(2026, 8, 19, 0, 0, 0)  # naive, 24h later
    assert hours_until(kickoff, now) == 24.0


def test_hours_until_handles_mixed_naive_and_aware():
    now = datetime(2026, 8, 18, 0, 0, 0, tzinfo=timezone.utc)
    kickoff = datetime(2026, 8, 18, 6, 0, 0)  # naive
    assert hours_until(kickoff, now) == 6.0


def test_maturity_never_claims_efficiency_in_label_text():
    for tier_result in (
        classify_market_maturity(n_bookmakers=1, hours_until_kickoff=None),
        classify_market_maturity(n_bookmakers=8, hours_until_kickoff=1.0, snapshot_count=10),
    ):
        assert "efficien" not in tier_result.label.lower()
