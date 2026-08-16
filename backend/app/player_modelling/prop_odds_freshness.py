"""Odds freshness classification (Section 11 of the automated-odds stage
brief) — "Do not rank stale prices as if they are current." Thresholds are
against `fetched_at` (our own recorded_at — see PlayerPropMarket's
docstring) rather than the bookmaker's own last_update, because our
fetch cadence (see prop_odds_quota.py's minimum refresh interval) is the
thing this app actually controls and can be held accountable to; a
bookmaker's last_update can be old simply because the price genuinely
hasn't moved, which isn't staleness on OUR part.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

FRESHNESS_FRESH = "fresh"
FRESHNESS_AGING = "aging"
FRESHNESS_STALE = "stale"

# Configurable thresholds (Section 11) - a stage-appropriate default, not
# hardcoded logic: pass different values to freshness_state for a market
# closer to first-bounce than these general defaults assume, if that's
# ever needed.
DEFAULT_FRESH_WITHIN = timedelta(hours=2)
DEFAULT_AGING_WITHIN = timedelta(hours=12)


@dataclass(frozen=True)
class FreshnessThresholds:
    fresh_within: timedelta = DEFAULT_FRESH_WITHIN
    aging_within: timedelta = DEFAULT_AGING_WITHIN


DEFAULT_THRESHOLDS = FreshnessThresholds()


def freshness_state(
    fetched_at: datetime, now: datetime | None = None, thresholds: FreshnessThresholds = DEFAULT_THRESHOLDS
) -> str:
    now = now or datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = now - fetched_at
    if age <= thresholds.fresh_within:
        return FRESHNESS_FRESH
    if age <= thresholds.aging_within:
        return FRESHNESS_AGING
    return FRESHNESS_STALE


def age_seconds(fetched_at: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return (now - fetched_at).total_seconds()
