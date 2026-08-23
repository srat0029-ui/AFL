"""Read-only Placed Bets analytics (personal record-keeping only - see
placed_bets.py's module docstring: never feeds model training, ranking,
or staking). Every split here is computed purely from already-settled
PlacedBet rows via plain arithmetic; nothing here re-settles, re-derives
a result, or touches any model/ranking code.
"""

from dataclasses import dataclass

from app.models import STATUS_LOST, STATUS_PENDING, STATUS_PUSH, STATUS_VOID, STATUS_WON, PlacedBet

# Below this many settled bets, a split is too small to say anything real
# about hit rate, edge, or model quality - the caller must present it as
# "Exploratory" rather than a real result (see module docstring).
MIN_SAMPLE_FOR_LABELED = 20

SOURCE_MODE_ORDER = ["high_probability", "best_value", "best_opportunity", "final_shortlist", "manual"]
MARKET_TYPE_ORDER = ["player_disposals", "player_goals", "h2h", "line", "total"]
CONFIDENCE_TIER_ORDER = ["higher_confidence", "moderate_confidence", "lower_confidence", "insufficient_history"]
PROBABILITY_BUCKETS = [
    (0.0, 0.5, "Under 50%"),
    (0.5, 0.6, "50-60%"),
    (0.6, 0.7, "60-70%"),
    (0.7, 0.8, "70-80%"),
    (0.8, 1.01, "80%+"),
]


@dataclass
class SplitResult:
    label: str
    n_settled: int
    wins: int
    losses: int
    voids: int
    hit_rate: float | None
    exploratory: bool


@dataclass
class PlacedBetAnalytics:
    n_total_settled: int
    wins: int
    losses: int
    voids: int
    hit_rate: float | None
    avg_odds_taken: float | None
    flat_stake_units: float | None
    flat_stake_roi_pct: float | None
    exploratory: bool
    min_sample_for_labeled: int
    by_source_mode: list[SplitResult]
    by_market_type: list[SplitResult]
    by_probability_bucket: list[SplitResult]
    by_confidence_tier: list[SplitResult]


def _split(bets: list[PlacedBet], label: str) -> SplitResult:
    wins = sum(1 for b in bets if b.status == STATUS_WON)
    losses = sum(1 for b in bets if b.status == STATUS_LOST)
    voids = sum(1 for b in bets if b.status in (STATUS_VOID, STATUS_PUSH))
    decided = wins + losses
    return SplitResult(
        label=label, n_settled=len(bets), wins=wins, losses=losses, voids=voids,
        hit_rate=(wins / decided) if decided > 0 else None,
        exploratory=len(bets) < MIN_SAMPLE_FOR_LABELED,
    )


def _group_and_split(bets: list[PlacedBet], key_fn, order: list[str] | None = None) -> list[SplitResult]:
    groups: dict[str, list[PlacedBet]] = {}
    for b in bets:
        key = key_fn(b)
        if key is None:
            continue
        groups.setdefault(key, []).append(b)
    keys = [k for k in order if k in groups] if order else sorted(groups.keys())
    return [_split(groups[k], k) for k in keys]


def _probability_bucket(bet: PlacedBet) -> str | None:
    for lo, hi, label in PROBABILITY_BUCKETS:
        if lo <= bet.model_probability < hi:
            return label
    return None


def compute_placed_bet_analytics(bets: list[PlacedBet]) -> PlacedBetAnalytics:
    settled = [b for b in bets if b.status != STATUS_PENDING]
    overall = _split(settled, "Overall")

    # Voids/pushes get their stake back - excluded from odds/ROI math
    # (which only makes sense for a bet that actually decided), but still
    # counted in n_settled/voids above.
    decided = [b for b in settled if b.status in (STATUS_WON, STATUS_LOST)]
    avg_odds_taken = (sum(b.odds_taken for b in decided) / len(decided)) if decided else None
    units = [(b.odds_taken - 1.0) if b.status == STATUS_WON else -1.0 for b in decided]
    flat_stake_units = sum(units) if decided else None
    flat_stake_roi_pct = (sum(units) / len(decided) * 100) if decided else None

    return PlacedBetAnalytics(
        n_total_settled=overall.n_settled, wins=overall.wins, losses=overall.losses, voids=overall.voids,
        hit_rate=overall.hit_rate, avg_odds_taken=avg_odds_taken, flat_stake_units=flat_stake_units,
        flat_stake_roi_pct=flat_stake_roi_pct, exploratory=overall.exploratory,
        min_sample_for_labeled=MIN_SAMPLE_FOR_LABELED,
        by_source_mode=_group_and_split(settled, lambda b: b.source_mode, SOURCE_MODE_ORDER),
        by_market_type=_group_and_split(settled, lambda b: b.market_type, MARKET_TYPE_ORDER),
        by_probability_bucket=_group_and_split(settled, _probability_bucket, [b[2] for b in PROBABILITY_BUCKETS]),
        by_confidence_tier=_group_and_split(settled, lambda b: b.confidence_tier, CONFIDENCE_TIER_ORDER),
    )
