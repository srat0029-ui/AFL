"""Tests for the extreme price-difference diagnostic (Market Integrity
stage, Section 2)."""

from datetime import datetime, timedelta, timezone

from app.player_modelling.price_integrity import diagnose_price_spread

NOW = datetime.now(timezone.utc)


def _opportunity(bookmakers):
    return {"bookmakers": bookmakers}


def _entry(name, price, *, recorded_at=NOW, freshness="fresh", eligibility="included", is_exchange=False):
    return {
        "bookmaker_name": name,
        "price_decimal": price,
        "recorded_at": recorded_at,
        "freshness": freshness,
        "eligibility": eligibility,
        "is_exchange": is_exchange,
    }


def test_no_diagnostic_below_loosest_threshold():
    o = _opportunity([_entry("A", 2.10), _entry("B", 2.00)])  # 5% gap
    assert diagnose_price_spread(o) is None


def test_flags_gap_above_15_percent_band():
    o = _opportunity([_entry("A", 2.40), _entry("B", 2.00)])  # 20% gap
    diag = diagnose_price_spread(o)
    assert diag is not None
    assert diag.band_pct == 15.0
    assert diag.passes_integrity is True  # both fresh, close together, no exchange


def test_flags_gap_above_30_and_50_percent_bands():
    o = _opportunity([_entry("A", 3.20), _entry("B", 2.00)])  # 60% gap
    diag = diagnose_price_spread(o)
    assert diag.band_pct == 50.0


def test_excludes_exchange_and_excluded_bookmakers_from_comparison():
    o = _opportunity([
        _entry("Betfair", 34.0, eligibility="informational_only", is_exchange=True),
        _entry("PointsBet", 19.0),
        _entry("TAB", 18.0),
    ])
    diag = diagnose_price_spread(o)
    # Betfair excluded entirely - compares PointsBet vs TAB only (~5.6%, below loosest band)
    assert diag is None


def test_fails_integrity_when_next_best_price_is_stale():
    o = _opportunity([
        _entry("A", 2.60, freshness="fresh"),
        _entry("B", 2.00, freshness="stale", recorded_at=NOW - timedelta(hours=20)),
    ])
    diag = diagnose_price_spread(o)
    assert diag is not None
    assert diag.passes_integrity is False
    assert any("stale" in issue for issue in diag.issues)


def test_fails_integrity_when_recorded_far_apart():
    o = _opportunity([
        _entry("A", 2.60, recorded_at=NOW),
        _entry("B", 2.00, recorded_at=NOW - timedelta(hours=10)),
    ])
    diag = diagnose_price_spread(o)
    assert diag is not None
    assert diag.passes_integrity is False
    assert diag.checks["recorded_close_together"] is False


def test_none_with_fewer_than_two_eligible_bookmakers():
    o = _opportunity([_entry("A", 2.60)])
    assert diagnose_price_spread(o) is None


def test_real_richmond_case_flagged_and_fails_integrity_when_stale_gap():
    # Real case from this stage's verification: PointsBet's price moved
    # ($19 -> $26) while other books' quotes were still the older snapshot.
    o = _opportunity([
        _entry("PointsBet", 26.0, freshness="fresh", recorded_at=NOW),
        _entry("TAB", 18.0, freshness="stale", recorded_at=NOW - timedelta(hours=16)),
    ])
    diag = diagnose_price_spread(o)
    assert diag is not None
    assert diag.band_pct == 30.0
    assert diag.passes_integrity is False
