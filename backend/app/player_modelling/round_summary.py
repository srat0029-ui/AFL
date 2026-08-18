"""Weekly Review round summary (Weekly Bet Review + Decision Support
stage, Section 17) — once a round's matches have completed and a
snapshot's items have been settled (weekly_shortlist_snapshot_service.
settle_snapshot), summarizes outcomes against what the Shortlist actually
showed at the time. Purely descriptive: a hypothetical flat-$1-per-item
result, never a suggestion to stake anything, and never fed back into
ranking or model tuning. Small-sample warnings are explicit and always
shown - one round is a handful of data points, not a validation set.
"""

from collections import Counter
from dataclasses import dataclass, field

from app.models import WeeklyShortlistSnapshot, WeeklyShortlistSnapshotItem

SMALL_SAMPLE_THRESHOLD = 10


@dataclass(frozen=True)
class ItemOutcome:
    label: str
    opportunity_type: str
    best_price: float
    best_bookmaker: str
    model_probability: float
    market_implied_probability: float
    match_result: str | None  # "won" | "lost" | "push" | None (unresolved)
    actual_stat_value: float | None
    flat_stake_pl: float | None  # profit/loss on a hypothetical flat $1 stake


def _flat_stake_pl(item: WeeklyShortlistSnapshotItem) -> float | None:
    if item.match_result == "won":
        return item.best_price - 1.0
    if item.match_result == "lost":
        return -1.0
    if item.match_result == "push":
        return 0.0
    return None


@dataclass(frozen=True)
class RoundSummary:
    snapshot_id: int
    round_number: int | None
    season_year: int | None
    n_items: int
    n_settled: int
    n_unresolved: int
    n_won: int
    n_lost: int
    n_push: int
    hypothetical_flat_stake_pl: float | None
    n_unique_matches: int
    n_team: int
    n_player: int
    confidence_tier_breakdown: dict[str, int]
    quality_tier_breakdown: dict[str, int]
    small_sample_warning: bool
    items: list[ItemOutcome] = field(default_factory=list)


def build_round_summary(snapshot: WeeklyShortlistSnapshot) -> RoundSummary:
    items = snapshot.items
    settled = [i for i in items if i.settled_at is not None]

    outcomes = [
        ItemOutcome(
            label=i.label, opportunity_type=i.opportunity_type, best_price=i.best_price, best_bookmaker=i.best_bookmaker,
            model_probability=i.model_probability, market_implied_probability=i.market_implied_probability,
            match_result=i.match_result, actual_stat_value=i.actual_stat_value, flat_stake_pl=_flat_stake_pl(i),
        )
        for i in items
    ]

    settled_pl = [o.flat_stake_pl for o in outcomes if o.flat_stake_pl is not None]

    return RoundSummary(
        snapshot_id=snapshot.id,
        round_number=snapshot.round_number,
        season_year=snapshot.season_year,
        n_items=len(items),
        n_settled=len(settled),
        n_unresolved=len(items) - len(settled),
        n_won=sum(1 for i in settled if i.match_result == "won"),
        n_lost=sum(1 for i in settled if i.match_result == "lost"),
        n_push=sum(1 for i in settled if i.match_result == "push"),
        hypothetical_flat_stake_pl=sum(settled_pl) if settled_pl else None,
        n_unique_matches=len({i.match_id for i in items}),
        n_team=sum(1 for i in items if i.opportunity_type == "team"),
        n_player=sum(1 for i in items if i.opportunity_type == "player"),
        confidence_tier_breakdown=dict(Counter(i.confidence_tier for i in items)),
        quality_tier_breakdown=dict(Counter(i.quality_tier for i in items)),
        small_sample_warning=len(settled) < SMALL_SAMPLE_THRESHOLD,
        items=outcomes,
    )


def round_summary_as_dict(s: RoundSummary) -> dict:
    return {
        "snapshot_id": s.snapshot_id,
        "round_number": s.round_number,
        "season_year": s.season_year,
        "n_items": s.n_items,
        "n_settled": s.n_settled,
        "n_unresolved": s.n_unresolved,
        "n_won": s.n_won,
        "n_lost": s.n_lost,
        "n_push": s.n_push,
        "hypothetical_flat_stake_pl": s.hypothetical_flat_stake_pl,
        "n_unique_matches": s.n_unique_matches,
        "n_team": s.n_team,
        "n_player": s.n_player,
        "confidence_tier_breakdown": s.confidence_tier_breakdown,
        "quality_tier_breakdown": s.quality_tier_breakdown,
        "small_sample_warning": s.small_sample_warning,
        "items": [
            {
                "label": o.label, "opportunity_type": o.opportunity_type, "best_price": o.best_price, "best_bookmaker": o.best_bookmaker,
                "model_probability": o.model_probability, "market_implied_probability": o.market_implied_probability,
                "match_result": o.match_result, "actual_stat_value": o.actual_stat_value, "flat_stake_pl": o.flat_stake_pl,
            }
            for o in s.items
        ],
    }
