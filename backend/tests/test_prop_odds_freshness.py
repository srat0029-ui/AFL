from datetime import datetime, timedelta, timezone

from app.player_modelling.prop_odds_freshness import (
    FRESHNESS_AGING,
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    FreshnessThresholds,
    freshness_state,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def test_freshness_fresh_within_default_threshold():
    assert freshness_state(NOW - timedelta(minutes=30), now=NOW) == FRESHNESS_FRESH


def test_freshness_aging_between_thresholds():
    assert freshness_state(NOW - timedelta(hours=5), now=NOW) == FRESHNESS_AGING


def test_freshness_stale_beyond_aging_threshold():
    assert freshness_state(NOW - timedelta(hours=24), now=NOW) == FRESHNESS_STALE


def test_freshness_exactly_at_boundary_counts_as_within():
    thresholds = FreshnessThresholds(fresh_within=timedelta(hours=1), aging_within=timedelta(hours=2))
    assert freshness_state(NOW - timedelta(hours=1), now=NOW, thresholds=thresholds) == FRESHNESS_FRESH
    assert freshness_state(NOW - timedelta(hours=2), now=NOW, thresholds=thresholds) == FRESHNESS_AGING


def test_freshness_handles_naive_datetime():
    naive = (NOW - timedelta(minutes=10)).replace(tzinfo=None)
    assert freshness_state(naive, now=NOW) == FRESHNESS_FRESH


def test_configurable_thresholds_change_classification():
    tight = FreshnessThresholds(fresh_within=timedelta(minutes=5), aging_within=timedelta(minutes=10))
    assert freshness_state(NOW - timedelta(minutes=8), now=NOW, thresholds=tight) == FRESHNESS_AGING
