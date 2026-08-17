"""Research/reporting layer over REAL logged PropMarketObservation rows
(Sections 9-15, 20 of the market-logging stage brief) — deliberately
separate from the existing synthetic historical backtesting modules
(disposal_backtest.py, goal_backtest.py): those evaluate the model against
2016-2025 AFL Tables results; this evaluates real bookmaker prices against
real settled outcomes, a dataset that starts essentially empty and grows
one match at a time. Never mix the two — see load_real_market_tracking_report's
docstring.

Pseudo-replication (Section 10): a single player-match at one bookmaker
typically produces MANY observations (one per alternate threshold, e.g. 31
disposal lines for one player in one match) that are NOT independent
evidence — they're mostly restatements of the same underlying projection
priced at different cutoffs. Every aggregate below reports both the raw
observation count AND the unique-player-match count, and callers should
treat the latter as the real sample size.

DO NOT USE THIS DATASET TO RETUNE THE MODEL (live-operations stage, Section
17 — read this before changing disposal_models.py, goal_models.py,
disposal_confidence.py/goal_confidence.py, prop_opportunity_ranking.py, or
prop_math.py's edge-category thresholds based on what this module reports).

The dataset this module reads is an EVALUATION dataset, not a development
one. The disposal/goal models were built and validated entirely on the
2016-2025 synthetic historical backtest (disposal_backtest.py/
goal_backtest.py) — a completely separate process with its own chronological
train/eval split (see those modules' docstrings). The real observations
logged here exist to eventually answer "were the model-market differences
we found actually useful," a question that can ONLY be answered honestly if
this data was never used to shape the thing being evaluated.

Repeatedly looking at a handful of early real results and nudging the
model, confidence thresholds, or ranking weights in response is a textbook
way to overfit to noise that looks like signal (Section 12's edge-bucket
warnings exist precisely because early buckets can be a handful of
observations) — and it would quietly convert this from an honest holdout
into a second training set, destroying the one dataset built specifically
to answer whether the model-market gap is real.

Any future model change must keep this separation explicit: retrain/
retune against the existing 2016-2025 historical development process only;
treat this module's output as a report card to read, never a training
signal to react to. If a change is ever justified by real-world results, it
must be documented as a deliberate, one-time, reasoned decision — not an
incremental drift driven by watching this page.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, PlayerPropMarket, PropMarketObservation

# Section 20: exploratory/low-confidence/still-developing/informative -
# UI/research framing only, never a formal significance claim.
SAMPLE_EXPLORATORY = "exploratory"
SAMPLE_LOW_CONFIDENCE = "low_confidence"
SAMPLE_STILL_DEVELOPING = "still_developing"
SAMPLE_INFORMATIVE = "informative"

SAMPLE_SIZE_LABELS = {
    SAMPLE_EXPLORATORY: "Exploratory only — fewer than 30 settled player-matches.",
    SAMPLE_LOW_CONFIDENCE: "Low-confidence evidence — fewer than 100 settled player-matches.",
    SAMPLE_STILL_DEVELOPING: "Still developing — fewer than 300 settled player-matches.",
    SAMPLE_INFORMATIVE: "Larger sample — increasingly informative, still not a formal significance test.",
}

_SETTLED_BINARY_RESULTS = {"won", "lost"}  # push/void/unresolved excluded from hit-rate/Brier/ROI


def sample_size_level(n_unique_player_matches: int) -> str:
    if n_unique_player_matches < 30:
        return SAMPLE_EXPLORATORY
    if n_unique_player_matches < 100:
        return SAMPLE_LOW_CONFIDENCE
    if n_unique_player_matches < 300:
        return SAMPLE_STILL_DEVELOPING
    return SAMPLE_INFORMATIVE


def _unique_player_matches(observations: list[PropMarketObservation]) -> int:
    return len({(o.player_id, o.match_id) for o in observations})


def _unique_player_matches_settled_binary(observations: list[PropMarketObservation]) -> int:
    """Section 20's sample-size thresholds are about SETTLED evidence
    specifically ("fewer than 30 settled observations") - a pending
    (unsettled) or push/void observation contributes no win/loss evidence,
    so it must not inflate the sample-size framing even though it's a
    perfectly real, correctly-logged row."""
    return len({(o.player_id, o.match_id) for o in observations if o.market_result in _SETTLED_BINARY_RESULTS})


@dataclass(frozen=True)
class DatasetSummary:
    total_observations: int
    settled_observations: int
    pending_observations: int
    unique_player_matches: int
    unique_players: int
    unique_matches: int
    unique_market_lines: int  # distinct (market_type, line_type, threshold)
    bookmakers: list[str]
    earliest_observed_at: datetime | None
    latest_observed_at: datetime | None


def dataset_summary(observations: list[PropMarketObservation]) -> DatasetSummary:
    settled = [o for o in observations if o.settled_at is not None]
    return DatasetSummary(
        total_observations=len(observations),
        settled_observations=len(settled),
        pending_observations=len(observations) - len(settled),
        unique_player_matches=_unique_player_matches(observations),
        unique_players=len({o.player_id for o in observations}),
        unique_matches=len({o.match_id for o in observations}),
        unique_market_lines=len({(o.market_type, o.line_type, o.threshold) for o in observations}),
        bookmakers=sorted({o.bookmaker.name for o in observations}),
        earliest_observed_at=min((o.observed_at for o in observations), default=None),
        latest_observed_at=max((o.observed_at for o in observations), default=None),
    )


def _outcome(o: PropMarketObservation) -> int | None:
    if o.market_result == "won":
        return 1
    if o.market_result == "lost":
        return 0
    return None  # push/void/unresolved - excluded from binary evaluation


def _brier(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - a) ** 2 for p, a in pairs) / len(pairs)


def _log_loss(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    eps = 1e-6
    total = 0.0
    for p, a in pairs:
        p_clipped = min(max(p, eps), 1 - eps)
        total += -(a * math.log(p_clipped) + (1 - a) * math.log(1 - p_clipped))
    return total / len(pairs)


@dataclass(frozen=True)
class ModelVsMarket:
    n_settled_binary: int
    model_brier: float | None
    model_log_loss: float | None
    market_brier: float | None
    market_log_loss: float | None
    market_probability_source: str  # "no-vig (devigged)" | "raw implied" - whichever was actually available/used


def model_vs_market(observations: list[PropMarketObservation]) -> ModelVsMarket:
    model_pairs: list[tuple[float, int]] = []
    market_pairs: list[tuple[float, int]] = []
    used_devig = False
    for o in observations:
        outcome = _outcome(o)
        if outcome is None:
            continue
        model_pairs.append((o.model_probability, outcome))
        market_prob = o.devigged_probability if o.devigged_probability is not None else o.raw_implied_probability
        if o.devigged_probability is not None:
            used_devig = True
        market_pairs.append((market_prob, outcome))
    return ModelVsMarket(
        n_settled_binary=len(model_pairs),
        model_brier=_brier(model_pairs),
        model_log_loss=_log_loss(model_pairs),
        market_brier=_brier(market_pairs),
        market_log_loss=_log_loss(market_pairs),
        market_probability_source="no-vig (devigged) where available, else raw implied" if used_devig else "raw implied (no devigged prices in this sample)",
    )


@dataclass(frozen=True)
class CalibrationBucket:
    probability_range: str
    n: int
    mean_predicted: float | None
    mean_actual: float | None


def calibration_buckets(pairs: list[tuple[float, int]], n_buckets: int = 5) -> list[CalibrationBucket]:
    """Coarse, sample-agnostic calibration table (predicted-probability
    deciles/quintiles vs actual hit rate) - deliberately simpler than the
    ECE machinery the synthetic backtests use (disposal_evaluation.py etc.),
    since this real dataset starts at a scale where a full ECE computation
    would be spurious precision, not more informative."""
    edges = [i / n_buckets for i in range(n_buckets + 1)]
    buckets = []
    for i in range(n_buckets):
        lo, hi = edges[i], edges[i + 1]
        in_bucket = [(p, a) for p, a in pairs if (lo <= p < hi) or (i == n_buckets - 1 and p == hi)]
        label = f"{lo * 100:.0f}-{hi * 100:.0f}%"
        if not in_bucket:
            buckets.append(CalibrationBucket(label, 0, None, None))
            continue
        buckets.append(
            CalibrationBucket(
                label, len(in_bucket),
                sum(p for p, _ in in_bucket) / len(in_bucket),
                sum(a for _, a in in_bucket) / len(in_bucket),
            )
        )
    return buckets


@dataclass(frozen=True)
class HypotheticalReturn:
    n_settled_binary: int
    n_pushed: int
    n_voided: int
    total_profit_flat_stake: float  # $1 stake per observation, on the "over"/"yes" side at offered_odds
    roi: float | None  # profit / n_settled_binary (pushes/voids excluded from the staked base)
    win_rate: float | None
    average_odds: float | None
    average_model_probability: float | None
    average_difference_pp: float | None


def hypothetical_return(observations: list[PropMarketObservation]) -> HypotheticalReturn:
    """A $1-flat-stake illustration of what these observations WOULD have
    returned, purely to make model-market differences legible while the
    dataset is small - never a staking recommendation (Section 21: no
    Kelly, no bankroll sizing, this module doesn't even expose a stake
    size parameter)."""
    binary = [o for o in observations if o.market_result in _SETTLED_BINARY_RESULTS]
    pushed = sum(1 for o in observations if o.market_result == "push")
    voided = sum(1 for o in observations if o.market_result == "void")
    if not binary:
        return HypotheticalReturn(0, pushed, voided, 0.0, None, None, None, None, None)

    profit = sum((o.offered_odds - 1.0) if o.market_result == "won" else -1.0 for o in binary)
    wins = sum(1 for o in binary if o.market_result == "won")
    return HypotheticalReturn(
        n_settled_binary=len(binary),
        n_pushed=pushed,
        n_voided=voided,
        total_profit_flat_stake=profit,
        roi=profit / len(binary),
        win_rate=wins / len(binary),
        average_odds=sum(o.offered_odds for o in binary) / len(binary),
        average_model_probability=sum(o.model_probability for o in binary) / len(binary),
        average_difference_pp=sum(o.difference_pp for o in binary) / len(binary),
    )


@dataclass(frozen=True)
class BucketResult:
    label: str
    n_observations: int
    n_unique_player_matches: int
    returns: HypotheticalReturn
    sample_size_level: str


EDGE_BUCKET_EDGES: list[tuple[float, float, str]] = [
    (-math.inf, 0.0, "≤0pp"),
    (0.0, 0.03, "0-3pp"),
    (0.03, 0.05, "3-5pp"),
    (0.05, 0.08, "5-8pp"),
    (0.08, 0.12, "8-12pp"),
    (0.12, math.inf, "12pp+"),
]


def edge_buckets(observations: list[PropMarketObservation]) -> list[BucketResult]:
    results = []
    for lo, hi, label in EDGE_BUCKET_EDGES:
        in_bucket = [o for o in observations if lo <= o.difference_pp < hi]
        n_pm = _unique_player_matches(in_bucket)
        level = sample_size_level(_unique_player_matches_settled_binary(in_bucket))
        results.append(BucketResult(label, len(in_bucket), n_pm, hypothetical_return(in_bucket), level))
    return results


CONFIDENCE_BUCKET_ORDER = ["higher_confidence", "moderate_confidence", "lower_confidence", "insufficient_history"]


def confidence_buckets(observations: list[PropMarketObservation]) -> list[BucketResult]:
    results = []
    for tier in CONFIDENCE_BUCKET_ORDER:
        in_bucket = [o for o in observations if o.confidence_tier == tier]
        n_pm = _unique_player_matches(in_bucket)
        level = sample_size_level(_unique_player_matches_settled_binary(in_bucket))
        results.append(BucketResult(tier, len(in_bucket), n_pm, hypothetical_return(in_bucket), level))
    return results


LINEUP_BUCKET_ORDER = ["confirmed_selected", "named_in_squad", "substitute", "emergency", "placeholder", "uncertain", "confirmed_out"]


def lineup_status_buckets(observations: list[PropMarketObservation]) -> list[BucketResult]:
    """Section 14: this real dataset today is entirely `placeholder`
    (no official team has been announced yet for the audited match) - the
    bucket table makes that fact impossible to miss rather than silently
    averaging placeholder-era and confirmed-era observations together."""
    results = []
    for status in LINEUP_BUCKET_ORDER:
        in_bucket = [o for o in observations if o.selection_status_at_observation == status]
        if not in_bucket:
            continue
        n_pm = _unique_player_matches(in_bucket)
        level = sample_size_level(_unique_player_matches_settled_binary(in_bucket))
        results.append(BucketResult(status, len(in_bucket), n_pm, hypothetical_return(in_bucket), level))
    return results


TIMING_BUCKET_EDGES: list[tuple[float, float, str]] = [
    (48.0, math.inf, "48h+"),
    (24.0, 48.0, "24-48h"),
    (6.0, 24.0, "6-24h"),
    (1.0, 6.0, "1-6h"),
    (0.0, 1.0, "<1h"),
]


def timing_buckets(db: Session, observations: list[PropMarketObservation]) -> list[BucketResult]:
    """Section 15: hours between observed_at and the match's kickoff."""
    match_kickoffs: dict[int, datetime] = {}
    for o in observations:
        if o.match_id not in match_kickoffs:
            match = db.get(Match, o.match_id)
            match_kickoffs[o.match_id] = match.scheduled_start

    def hours_before(o: PropMarketObservation) -> float:
        kickoff = match_kickoffs[o.match_id]
        observed = o.observed_at if o.observed_at.tzinfo else o.observed_at.replace(tzinfo=timezone.utc)
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        return (kickoff - observed).total_seconds() / 3600.0

    results = []
    for lo, hi, label in TIMING_BUCKET_EDGES:
        in_bucket = [o for o in observations if lo <= hours_before(o) < hi]
        n_pm = _unique_player_matches(in_bucket)
        level = sample_size_level(_unique_player_matches_settled_binary(in_bucket))
        results.append(BucketResult(label, len(in_bucket), n_pm, hypothetical_return(in_bucket), level))
    return results


# --- Observation coverage + market-open timing (live-operations stage,
# Sections 10-11) ------------------------------------------------------------


@dataclass(frozen=True)
class CoverageMetrics:
    """Section 10: is the collected dataset dense enough to be useful? Raw
    quotes vs. frozen observations are reported separately because they can
    legitimately diverge (a quote with no live projection yet doesn't get an
    observation — see prop_observation.py's skipped_no_projection)."""

    total_raw_quotes: int  # PlayerPropMarket rows, automated sources only
    frozen_observations: int
    unique_player_matches: int
    unique_matches: int
    unique_market_lines: int  # distinct (market_type, line_type, threshold)
    bookmakers: list[str]
    market_families: list[str]  # distinct market_type
    average_snapshots_per_player_market: float | None  # observations / distinct (player, match, bookmaker, market_type, line_type, threshold)


def coverage_metrics(db: Session, observations: list[PropMarketObservation], *, match_id: int | None = None, market_type: str | None = None) -> CoverageMetrics:
    quote_stmt = select(PlayerPropMarket).where(PlayerPropMarket.source != "manual")
    if match_id is not None:
        quote_stmt = quote_stmt.where(PlayerPropMarket.match_id == match_id)
    if market_type is not None:
        quote_stmt = quote_stmt.where(PlayerPropMarket.market_type == market_type)
    total_raw_quotes = len(db.scalars(quote_stmt).all())

    player_market_lines = {(o.player_id, o.match_id, o.bookmaker_id, o.market_type, o.line_type, o.threshold) for o in observations}
    avg_snapshots = len(observations) / len(player_market_lines) if player_market_lines else None

    return CoverageMetrics(
        total_raw_quotes=total_raw_quotes,
        frozen_observations=len(observations),
        unique_player_matches=_unique_player_matches(observations),
        unique_matches=len({o.match_id for o in observations}),
        unique_market_lines=len({(o.market_type, o.line_type, o.threshold) for o in observations}),
        bookmakers=sorted({o.bookmaker.name for o in observations}),
        market_families=sorted({o.market_type for o in observations}),
        average_snapshots_per_player_market=avg_snapshots,
    )


@dataclass(frozen=True)
class MarketOpenTiming:
    """Section 11: per logged market line, when did we start/stop seeing
    prices, and how much did the price actually move? `n_price_changes`
    counts DISTINCT offered_odds values, not raw observation count — many
    observations can share the same price (an unchanged quote re-observed
    across cycles isn't a price change)."""

    player_id: int
    player_name: str
    match_id: int
    bookmaker_id: int
    bookmaker_name: str
    market_type: str
    line_type: str
    threshold: float
    first_observed_at: datetime
    first_hours_before_kickoff: float
    latest_observed_at: datetime
    latest_hours_before_kickoff: float
    n_price_changes: int
    n_observations: int


def market_open_timing(db: Session, observations: list[PropMarketObservation]) -> list[MarketOpenTiming]:
    match_kickoffs: dict[int, datetime] = {}

    def hours_before(match_id: int, observed_at: datetime) -> float:
        if match_id not in match_kickoffs:
            match_kickoffs[match_id] = db.get(Match, match_id).scheduled_start
        kickoff = match_kickoffs[match_id]
        observed = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=timezone.utc)
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        return (kickoff - observed).total_seconds() / 3600.0

    groups: dict[tuple, list[PropMarketObservation]] = {}
    for o in observations:
        key = (o.player_id, o.match_id, o.bookmaker_id, o.market_type, o.line_type, o.threshold)
        groups.setdefault(key, []).append(o)

    results = []
    for (pid, mid, bid, market_type, line_type, threshold), obs in groups.items():
        obs_sorted = sorted(obs, key=lambda o: o.observed_at)
        first, latest = obs_sorted[0], obs_sorted[-1]
        results.append(
            MarketOpenTiming(
                player_id=pid, player_name=first.player.display_name, match_id=mid,
                bookmaker_id=bid, bookmaker_name=first.bookmaker.name,
                market_type=market_type, line_type=line_type, threshold=threshold,
                first_observed_at=first.observed_at, first_hours_before_kickoff=hours_before(mid, first.observed_at),
                latest_observed_at=latest.observed_at, latest_hours_before_kickoff=hours_before(mid, latest.observed_at),
                n_price_changes=len({o.offered_odds for o in obs}), n_observations=len(obs),
            )
        )
    return results


@dataclass(frozen=True)
class RealMarketTrackingReport:
    label: str
    summary: DatasetSummary
    model_vs_market: ModelVsMarket
    model_calibration: list[CalibrationBucket]
    market_calibration: list[CalibrationBucket]
    overall_return: HypotheticalReturn
    edge_buckets: list[BucketResult]
    confidence_buckets: list[BucketResult]
    lineup_buckets: list[BucketResult]
    timing_buckets: list[BucketResult]
    overall_sample_level: str
    coverage: CoverageMetrics
    market_open_timing: list[MarketOpenTiming]


def load_real_market_tracking_report(
    db: Session, *, match_id: int | None = None, market_type: str | None = None
) -> RealMarketTrackingReport:
    """The single entry point the API/UI layer should call. Deliberately
    NOT combined with anything from disposal_backtest.py/goal_backtest.py
    (the synthetic 2016-2025 evaluation) — this is real logged data only,
    labelled as such throughout (Section 9)."""
    stmt = select(PropMarketObservation)
    if match_id is not None:
        stmt = stmt.where(PropMarketObservation.match_id == match_id)
    if market_type is not None:
        stmt = stmt.where(PropMarketObservation.market_type == market_type)
    observations = db.scalars(stmt).all()

    mvm = model_vs_market(observations)
    model_pairs = [(o.model_probability, _outcome(o)) for o in observations if _outcome(o) is not None]
    market_pairs = [
        (o.devigged_probability if o.devigged_probability is not None else o.raw_implied_probability, _outcome(o))
        for o in observations if _outcome(o) is not None
    ]

    summary = dataset_summary(observations)
    return RealMarketTrackingReport(
        label="Real logged market observations",
        summary=summary,
        model_vs_market=mvm,
        model_calibration=calibration_buckets(model_pairs),
        market_calibration=calibration_buckets(market_pairs),
        overall_return=hypothetical_return(observations),
        edge_buckets=edge_buckets(observations),
        confidence_buckets=confidence_buckets(observations),
        lineup_buckets=lineup_status_buckets(observations),
        timing_buckets=timing_buckets(db, observations),
        overall_sample_level=sample_size_level(_unique_player_matches_settled_binary(observations)),
        coverage=coverage_metrics(db, observations, match_id=match_id, market_type=market_type),
        market_open_timing=market_open_timing(db, observations),
    )
