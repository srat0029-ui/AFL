"""Prospective Live Evaluation (Model Registry stage) — reads ONLY
PricingSnapshot rows: predictions frozen before kickoff, settled against
real outcomes after, never overwritten (see app/models/pricing_snapshot.py
and app/pricing/snapshot_service.py). Strictly separate from historical
backtest metrics (PlayerModelValidationMetric/GoalModelValidationMetric,
computed on 2016-2025 data) — this module never reads those tables, and
nothing here should ever be presented next to a backtest number without
the "Historical backtest" / "Prospective live evaluation" labels the API
schema carries explicitly for exactly this reason.

Market-consensus Brier/log-loss use `market_consensus_probability`, which
is FROZEN on the snapshot at generation time (see snapshot_service.py) —
never a probability re-derived from today's market state, so this is a
genuine same-moment model-vs-market comparison, not a mismatched one.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelling.metrics import brier_score, expected_calibration_error, calibration_table, log_loss
from app.models import PricingSnapshot
from app.player_modelling.placed_bet_analytics import PROBABILITY_BUCKETS

OUTCOME_TO_BINARY = {"won": 1.0, "lost": 0.0}

# Below this many settled, scored observations, a split is too small to
# report a real Brier/log-loss number — surfaced as "accumulating" instead
# of a misleadingly precise figure (same discipline as
# placed_bet_analytics.py's MIN_SAMPLE_FOR_LABELED).
MIN_SAMPLE_FOR_LABELED = 30


@dataclass(frozen=True)
class ProspectiveSplit:
    label: str
    n_settled: int
    n_unique_events: int
    model_brier: float | None
    market_brier: float | None
    model_log_loss: float | None
    market_log_loss: float | None
    model_calibration_ece: float | None
    n_with_market_consensus: int
    exploratory: bool


@dataclass(frozen=True)
class ProspectiveEvaluationReport:
    has_settled_data: bool
    n_frozen_total: int
    n_settled: int
    n_unique_player_match_events: int
    overall: ProspectiveSplit | None
    by_market_family: list[ProspectiveSplit]
    by_probability_bucket: list[ProspectiveSplit]
    by_model_version: list[ProspectiveSplit]
    message: str


def _scoreable(snaps: list[PricingSnapshot]) -> list[PricingSnapshot]:
    """Only won/lost rows carry a real binary outcome to score a
    probability against — push/void are excluded from Brier/log-loss/ECE
    (same convention as placed_bet_analytics.py's decided-bets scoping)."""
    return [s for s in snaps if s.outcome in OUTCOME_TO_BINARY]


def _split(snaps: list[PricingSnapshot], label: str) -> ProspectiveSplit:
    scoreable = _scoreable(snaps)
    n_events = len({(s.player_id, s.match_id) for s in snaps})
    if not scoreable:
        return ProspectiveSplit(
            label=label, n_settled=len(snaps), n_unique_events=n_events, model_brier=None, market_brier=None,
            model_log_loss=None, market_log_loss=None, model_calibration_ece=None, n_with_market_consensus=0,
            exploratory=True,
        )

    model_probs = [s.model_probability for s in scoreable]
    outcomes = [OUTCOME_TO_BINARY[s.outcome] for s in scoreable]
    cal = calibration_table(model_probs, outcomes, n_bins=10)

    with_consensus = [s for s in scoreable if s.market_consensus_probability is not None]
    market_brier = market_ll = None
    if with_consensus:
        m_probs = [s.market_consensus_probability for s in with_consensus]
        m_outcomes = [OUTCOME_TO_BINARY[s.outcome] for s in with_consensus]
        market_brier = brier_score(m_probs, m_outcomes)
        market_ll = log_loss(m_probs, m_outcomes)

    return ProspectiveSplit(
        label=label, n_settled=len(snaps), n_unique_events=n_events,
        model_brier=brier_score(model_probs, outcomes), market_brier=market_brier,
        model_log_loss=log_loss(model_probs, outcomes), market_log_loss=market_ll,
        model_calibration_ece=expected_calibration_error(cal), n_with_market_consensus=len(with_consensus),
        exploratory=len(scoreable) < MIN_SAMPLE_FOR_LABELED,
    )


def _group_and_split(snaps: list[PricingSnapshot], key_fn, order: list[str] | None = None) -> list[ProspectiveSplit]:
    groups: dict[str, list[PricingSnapshot]] = {}
    for s in snaps:
        key = key_fn(s)
        if key is None:
            continue
        groups.setdefault(key, []).append(s)
    keys = [k for k in order if k in groups] if order else sorted(groups.keys())
    return [_split(groups[k], k) for k in keys]


def _probability_bucket(snap: PricingSnapshot) -> str | None:
    for lo, hi, label in PROBABILITY_BUCKETS:
        if lo <= snap.model_probability < hi:
            return label
    return None


def load_prospective_evaluation(db: Session) -> ProspectiveEvaluationReport:
    all_snaps = db.scalars(select(PricingSnapshot)).all()
    settled = [s for s in all_snaps if s.outcome is not None]

    if not settled:
        return ProspectiveEvaluationReport(
            has_settled_data=False, n_frozen_total=len(all_snaps), n_settled=0, n_unique_player_match_events=0,
            overall=None, by_market_family=[], by_probability_bucket=[], by_model_version=[],
            message=(
                f"Accumulating data — {len(all_snaps):,} price(s) frozen before kickoff so far, "
                "none settled yet. Prospective evaluation will populate automatically as matches complete."
            ),
        )

    overall = _split(settled, "Overall")
    message = (
        f"{overall.exploratory and 'Exploratory — ' or ''}{len(settled):,} settled prediction(s) "
        f"({overall.n_unique_events:,} unique player-match/event(s))."
    )
    return ProspectiveEvaluationReport(
        has_settled_data=True, n_frozen_total=len(all_snaps), n_settled=len(settled),
        n_unique_player_match_events=overall.n_unique_events, overall=overall,
        by_market_family=_group_and_split(settled, lambda s: s.market_family, ["team", "player_disposals", "player_goals"]),
        by_probability_bucket=_group_and_split(settled, _probability_bucket, [b[2] for b in PROBABILITY_BUCKETS]),
        by_model_version=_group_and_split(settled, lambda s: s.model_version),
        message=message,
    )
