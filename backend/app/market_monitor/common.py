"""Small shared helpers used by every detector module — deliberately thin
so the actual detection logic in each module reads as the real content,
not adapter boilerplate. Reuses app.pricing.market_intelligence's
MarketIntelligence (team_market_intelligence/player_market_intelligence)
as THE consensus engine (item 3): it already excludes exchanges (via
Bookmaker.eligibility, see bookmaker_classification.py), takes the latest
quote per bookmaker, devigs where a same-book paired side exists (see
consensus_and_outliers.py), and reports mean probability + spread + count.
The only thing added here is MEDIAN (item 3 asks for median AND mean) and
per-book freshness annotation.
"""

import statistics
from datetime import datetime, timezone

from app.pricing.market_intelligence import BookLine, MarketIntelligence
from app.market_monitor.types import BookmakerPriceEntry, ModelRiskFlagEntry


def median_probability(intel: MarketIntelligence) -> float | None:
    if intel.consensus is None or not intel.consensus.per_bookmaker:
        return None
    return statistics.median(e.probability for e in intel.consensus.per_bookmaker)


def bookmaker_price_entries(books: list[BookLine]) -> list[BookmakerPriceEntry]:
    return [BookmakerPriceEntry(bookmaker_name=b.bookmaker_name, price_decimal=b.price_decimal, recorded_at=b.recorded_at, eligibility=b.eligibility) for b in books]


def dedupe_bookmaker_prices(entries) -> list:
    """Multiple alerts on the SAME case each carry their own copy of that
    market's bookmaker list (see detector.py's _divergence_and_outlier_and_dispersion_alerts,
    which attaches the same `books` to every alert type it produces) - a
    case's merged evidence must show each bookmaker once, not once per
    contributing alert. Keeps the latest-recorded entry per bookmaker."""
    latest: dict[str, object] = {}
    for e in entries:
        cur = latest.get(e.bookmaker_name)
        if cur is None or e.recorded_at > cur.recorded_at:
            latest[e.bookmaker_name] = e
    return sorted(latest.values(), key=lambda e: e.bookmaker_name)


def model_risk_flag_entries(flags) -> list[ModelRiskFlagEntry]:
    return [ModelRiskFlagEntry(code=f.code, description=f.description) for f in flags]


def aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo across a round trip — every timestamp this app
    stores is genuinely UTC (see live_cycle.py's identical helper)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
