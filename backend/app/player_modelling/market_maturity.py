"""Market maturity indicator (Market Integrity + Final Weekly Picks stage,
Section 12) — DESCRIPTIVE ONLY. Early/Developing/Mature describes how
much price-discovery activity has been observed for a market (how many
bookmakers are quoting it, how many price snapshots exist, how close to
kickoff it is), never a claim that a "mature" market is more efficient or
harder to beat — the brief is explicit on this, and nothing here feeds
into opportunity_score or any ranking.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

EARLY_MARKET = "early_market"
DEVELOPING_MARKET = "developing_market"
MATURE_MARKET = "mature_market"

MATURITY_LABELS = {
    EARLY_MARKET: "Early market",
    DEVELOPING_MARKET: "Developing market",
    MATURE_MARKET: "Mature market",
}

# Breadth/history thresholds — deliberately simple, transparent, and
# threshold-based (not a learned/statistical measure) per Section 12's
# "descriptive only" requirement.
_MATURE_MIN_BOOKMAKERS = 6
_DEVELOPING_MIN_BOOKMAKERS = 3
_MATURE_MIN_SNAPSHOTS = 3
_CLOSE_TO_KICKOFF_HOURS = 48.0


@dataclass(frozen=True)
class MarketMaturity:
    tier: str
    label: str
    n_bookmakers: int
    snapshot_count: int | None
    hours_until_kickoff: float | None


def hours_until(kickoff: datetime, now: datetime) -> float:
    # SQLite (this project's DB) does not preserve tzinfo on read even for
    # DateTime(timezone=True) columns — every other naive/aware comparison
    # in this app (see prop_odds_freshness.freshness_state) treats a naive
    # value as UTC, so this matches that established convention.
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (kickoff - now).total_seconds() / 3600.0


def classify_market_maturity(
    *, n_bookmakers: int, hours_until_kickoff: float | None, snapshot_count: int | None = None
) -> MarketMaturity:
    score = 0
    if n_bookmakers >= _MATURE_MIN_BOOKMAKERS:
        score += 2
    elif n_bookmakers >= _DEVELOPING_MIN_BOOKMAKERS:
        score += 1

    if snapshot_count is not None and snapshot_count >= _MATURE_MIN_SNAPSHOTS:
        score += 1

    if hours_until_kickoff is not None and hours_until_kickoff <= _CLOSE_TO_KICKOFF_HOURS:
        score += 1

    if score >= 3:
        tier = MATURE_MARKET
    elif score >= 1:
        tier = DEVELOPING_MARKET
    else:
        tier = EARLY_MARKET

    return MarketMaturity(
        tier=tier,
        label=MATURITY_LABELS[tier],
        n_bookmakers=n_bookmakers,
        snapshot_count=snapshot_count,
        hours_until_kickoff=hours_until_kickoff,
    )


def maturity_as_dict(m: MarketMaturity) -> dict:
    return {
        "tier": m.tier,
        "label": m.label,
        "n_bookmakers": m.n_bookmakers,
        "snapshot_count": m.snapshot_count,
        "hours_until_kickoff": m.hours_until_kickoff,
    }
