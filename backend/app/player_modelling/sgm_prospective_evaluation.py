"""Prospective Live Evaluation for Same Game Multi joint pricing — reads
ONLY SgmPriceSnapshot rows (see app/models/sgm_price_snapshot.py and
app/pricing/sgm_snapshot_service.py). Same shape and discipline as
app/player_modelling/prospective_evaluation.py (MIN_SAMPLE_FOR_LABELED=30,
exploratory tagging, honest "accumulating data" state), but a separate
module rather than a shared import: the row type is different (multi-leg,
not single-selection) and the core comparison is different — model vs
NAIVE INDEPENDENCE (always computable) and model vs a genuine BOOKMAKER SGM
price (today always "no data", see below), not model vs market consensus.

Two behaviours worth being explicit about:

1. `overall`/`by_n_legs`/`by_leg_combination`/`by_correlation_adjustment_
   magnitude` score only the CLOSING snapshot per real combo (the settled
   row with the lowest hours_to_kickoff for a given (match_id,
   leg_signature)) — deliberately avoiding pseudo-replication. The same
   real combo can be frozen up to four times (once per snapshot_horizon)
   across the pre-match window; scoring every one of those as an
   independent Brier/log-loss observation would count the same match
   outcome multiple times against highly-correlated probability estimates,
   inflating the apparent sample size (the exact failure mode
   scripts/sgm_correlation_research.py already flagged for pseudo-
   replicated teammate pairs).

2. `by_snapshot_horizon` is the one split that DELIBERATELY does NOT
   dedupe — the entire point is comparing calibration across horizons, so
   each bucket is scored on every settled row that landed in it (a given
   combo can contribute to at most one row per bucket, by the snapshot
   table's own uniqueness constraint, so there's no duplication WITHIN a
   bucket either).

`bookmaker_brier`/`bookmaker_log_loss`/`n_with_bookmaker_price` are always
None/0 as of this write-up: no odds provider integration in this codebase
ingests a genuine bookmaker Same Game Multi price (see
app/models/sgm_price_snapshot.py's module docstring). The fields exist so
this activates automatically if that ever changes, not because the data
exists today.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelling.metrics import brier_score, calibration_table, expected_calibration_error, log_loss
from app.models import SgmPriceSnapshot

OUTCOME_TO_BINARY = {"won": 1.0, "lost": 0.0}

MIN_SAMPLE_FOR_LABELED = 30  # same discipline as prospective_evaluation.py / placed_bet_analytics.py

# Reporting buckets only (not a pricing threshold) - same style as
# app/player_modelling/placed_bet_analytics.py's PROBABILITY_BUCKETS.
# Effect sizes validated in scripts/sgm_joint_model_backtest.py were small
# (a fraction of a percentage point in most cases), so the bands are drawn
# tight near zero rather than mirroring PROBABILITY_BUCKETS' coarser spacing.
CORRELATION_ADJUSTMENT_BUCKETS: list[tuple[float, float, str]] = [
    (0.0, 0.5, "Negligible (<0.5pp)"),
    (0.5, 2.0, "Moderate (0.5-2pp)"),
    (2.0, float("inf"), "Large (2pp+)"),
]


@dataclass(frozen=True)
class SgmProspectiveSplit:
    label: str
    n_settled: int
    n_unique_combos: int
    model_brier: float | None
    naive_brier: float | None
    model_log_loss: float | None
    naive_log_loss: float | None
    model_calibration_ece: float | None
    bookmaker_brier: float | None
    bookmaker_log_loss: float | None
    n_with_bookmaker_price: int
    exploratory: bool


@dataclass(frozen=True)
class SgmProspectiveEvaluationReport:
    has_settled_data: bool
    n_frozen_total: int
    n_settled: int
    n_unique_combos: int
    overall: SgmProspectiveSplit | None
    by_n_legs: list[SgmProspectiveSplit]
    by_leg_combination: list[SgmProspectiveSplit]
    by_correlation_adjustment_magnitude: list[SgmProspectiveSplit]
    by_snapshot_horizon: list[SgmProspectiveSplit]
    message: str


def _scoreable(snaps: list[SgmPriceSnapshot]) -> list[SgmPriceSnapshot]:
    return [s for s in snaps if s.outcome in OUTCOME_TO_BINARY]


def _closing_snapshot_per_combo(settled: list[SgmPriceSnapshot]) -> list[SgmPriceSnapshot]:
    """One row per real (match, leg_signature) combo - whichever settled
    snapshot had the lowest hours_to_kickoff (closest to, or into, the
    match) - so aggregate splits never pseudo-replicate the same outcome."""
    best: dict[tuple[int, str], SgmPriceSnapshot] = {}
    for s in settled:
        key = (s.match_id, s.leg_signature)
        if key not in best or s.hours_to_kickoff < best[key].hours_to_kickoff:
            best[key] = s
    return list(best.values())


def _split(snaps: list[SgmPriceSnapshot], label: str) -> SgmProspectiveSplit:
    scoreable = _scoreable(snaps)
    n_unique = len({(s.match_id, s.leg_signature) for s in snaps})
    if not scoreable:
        return SgmProspectiveSplit(
            label=label, n_settled=len(snaps), n_unique_combos=n_unique, model_brier=None, naive_brier=None,
            model_log_loss=None, naive_log_loss=None, model_calibration_ece=None, bookmaker_brier=None,
            bookmaker_log_loss=None, n_with_bookmaker_price=0, exploratory=True,
        )

    model_probs = [s.model_probability for s in scoreable]
    naive_probs = [s.naive_independence_probability for s in scoreable]
    outcomes = [OUTCOME_TO_BINARY[s.outcome] for s in scoreable]
    cal = calibration_table(model_probs, outcomes, n_bins=10)

    with_bookmaker = [s for s in scoreable if s.bookmaker_implied_probability is not None]
    bookmaker_brier = bookmaker_ll = None
    if with_bookmaker:
        b_probs = [s.bookmaker_implied_probability for s in with_bookmaker]
        b_outcomes = [OUTCOME_TO_BINARY[s.outcome] for s in with_bookmaker]
        bookmaker_brier = brier_score(b_probs, b_outcomes)
        bookmaker_ll = log_loss(b_probs, b_outcomes)

    return SgmProspectiveSplit(
        label=label, n_settled=len(snaps), n_unique_combos=n_unique,
        model_brier=brier_score(model_probs, outcomes), naive_brier=brier_score(naive_probs, outcomes),
        model_log_loss=log_loss(model_probs, outcomes), naive_log_loss=log_loss(naive_probs, outcomes),
        model_calibration_ece=expected_calibration_error(cal),
        bookmaker_brier=bookmaker_brier, bookmaker_log_loss=bookmaker_ll, n_with_bookmaker_price=len(with_bookmaker),
        exploratory=len(scoreable) < MIN_SAMPLE_FOR_LABELED,
    )


def _group_and_split(snaps: list[SgmPriceSnapshot], key_fn, order: list[str] | None = None) -> list[SgmProspectiveSplit]:
    groups: dict[str, list[SgmPriceSnapshot]] = {}
    for s in snaps:
        key = key_fn(s)
        if key is None:
            continue
        groups.setdefault(key, []).append(s)
    keys = [k for k in order if k in groups] if order else sorted(groups.keys())
    return [_split(groups[k], k) for k in keys]


def _correlation_adjustment_bucket(snap: SgmPriceSnapshot) -> str | None:
    magnitude = abs(snap.correlation_adjustment_pp)
    for lo, hi, label in CORRELATION_ADJUSTMENT_BUCKETS:
        if lo <= magnitude < hi:
            return label
    return None


def load_sgm_prospective_evaluation(db: Session) -> SgmProspectiveEvaluationReport:
    all_snaps = db.scalars(select(SgmPriceSnapshot)).all()
    settled = [s for s in all_snaps if s.outcome is not None]

    if not settled:
        return SgmProspectiveEvaluationReport(
            has_settled_data=False, n_frozen_total=len(all_snaps), n_settled=0, n_unique_combos=0,
            overall=None, by_n_legs=[], by_leg_combination=[], by_correlation_adjustment_magnitude=[], by_snapshot_horizon=[],
            message=(
                f"Accumulating data — {len(all_snaps):,} Same Game Multi price(s) frozen before kickoff so far, "
                "none settled yet. Prospective evaluation will populate automatically as matches complete."
            ),
        )

    closing = _closing_snapshot_per_combo(settled)
    overall = _split(closing, "Overall")
    message = (
        f"{'Exploratory — ' if overall.exploratory else ''}{overall.n_settled:,} settled combo(s) "
        f"({len(closing):,} unique, deduped to each combo's closing snapshot)."
    )

    return SgmProspectiveEvaluationReport(
        has_settled_data=True, n_frozen_total=len(all_snaps), n_settled=len(settled), n_unique_combos=len(closing),
        overall=overall,
        by_n_legs=_group_and_split(closing, lambda s: str(s.n_legs)),
        by_leg_combination=_group_and_split(closing, lambda s: s.leg_type_combination),
        by_correlation_adjustment_magnitude=_group_and_split(closing, _correlation_adjustment_bucket, [b[2] for b in CORRELATION_ADJUSTMENT_BUCKETS]),
        by_snapshot_horizon=_group_and_split(settled, lambda s: s.snapshot_horizon, ["24h_plus", "6h_24h", "1h_6h", "under_1h"]),
        message=message,
    )
