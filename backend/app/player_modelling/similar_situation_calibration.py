"""Similar-situation historical calibration (Weekly Bet Review stage,
Section 4) — "where defensible, show how the model historically performed
in similar situations," using MODEL-LEVEL probability-band buckets, never
a specific team's or player's own history (that would be cherry-picking
one favourable data point, not a real calibration check).

Team H2H: buckets the SAME raw Elo evaluation-period predictions already
computed for model_strength_context.py (no new backtest run) by 5-point-
wide raw predicted-probability bands (0-5%, 5-10%, ..., 95-100%) so a
45-50% prediction lands in its own exact band, matching this stage's own
worked example.

Player disposals/goals: reuses the already-persisted eval-period
predictions (PlayerDisposalPrediction / PlayerGoalPrediction) for the
promoted model, recomputes each row's probability of clearing the NEAREST
reference threshold (same threshold set model_strength_context.py already
uses) via the same NB/hurdle distribution machinery live_report_query.py
uses for a live projection, and buckets in 10-point-wide bands (matching
this stage's own "60-70%" worked example). Goals require a materially
larger minimum sample than disposals before a band is shown, since goal
thresholds are rarer events.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtesting.evaluation import EVALUATION_START_YEAR
from app.modelling.data_loading import load_completed_matches
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.models import GoalModelRun, ModelRun, PlayerDisposalPrediction, PlayerGoalPrediction, PlayerModelRun
from app.player_modelling.live_report_query import (
    _DISPOSAL_CALIBRATION_THRESHOLDS,
    _GOAL_CALIBRATION_THRESHOLDS,
    disposal_distribution_for,
    goal_distribution_for,
    price_line,
)

MIN_H2H_BAND_SAMPLE = 30
MIN_DISPOSAL_BAND_SAMPLE = 100
MIN_GOAL_BAND_SAMPLE = 300  # Section 4: "stronger minimum-sample requirements" for goals

_H2H_CACHE: list[dict] | None = None
_H2H_CACHE_ATTEMPTED = False


def _bucket_predictions(probs: list[float], outcomes: list[float], n_bins: int) -> list[dict]:
    """A numeric-bounds equivalent of app.modelling.metrics.calibration_table
    - that function's bucket labels are formatted with a fixed 1 decimal
    place, which silently collapses distinct bins when n_bins > 10 (e.g.
    45%-50% and 40%-45% both render as "0.4-0.5"). Keeping lo/hi as floats
    here (never round-tripped through a formatted string) avoids that
    precision loss for this module's 5-point-wide H2H bands."""
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        buckets[idx].append((p, o))

    rows = []
    for i, bucket in enumerate(buckets):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not bucket:
            rows.append({"lo": lo, "hi": hi, "n": 0, "avg_predicted": None, "actual_rate": None})
            continue
        rows.append(
            {
                "lo": lo,
                "hi": hi,
                "n": len(bucket),
                "avg_predicted": sum(p for p, _ in bucket) / len(bucket),
                "actual_rate": sum(o for _, o in bucket) / len(bucket),
            }
        )
    return rows

_disposal_band_cache: dict[float, list[dict]] = {}
_goal_band_cache: dict[float, list[dict]] = {}


@dataclass(frozen=True)
class CalibrationBand:
    band_label: str  # e.g. "45%-50%"
    avg_predicted: float | None
    actual_rate: float | None
    n: int
    meets_min_sample: bool


def _band_containing(rows: list[dict], probability: float) -> dict | None:
    for row in rows:
        if row["lo"] <= probability < row["hi"] or (row["hi"] >= 1.0 and probability >= row["hi"]):
            return row
    return rows[-1] if rows else None


def _get_h2h_raw_predictions(db: Session) -> list[dict] | None:
    global _H2H_CACHE, _H2H_CACHE_ATTEMPTED
    if _H2H_CACHE_ATTEMPTED:
        return _H2H_CACHE
    _H2H_CACHE_ATTEMPTED = True
    run = db.scalar(select(ModelRun).where(ModelRun.model_name == "elo"))
    if run is None:
        return None
    matches = load_completed_matches(db)
    config = EloConfig(**run.config_json)
    predictions = elo_walk_forward(matches, config)
    # Match app/backtesting/evaluation.py's own warmup/evaluation split so
    # this reflects the SAME evaluation-period Elo already reports
    # elsewhere, not a differently-scoped recomputation.
    evaluation = [p for p in predictions if p.season_year >= EVALUATION_START_YEAR]
    probs = [p.home_win_probability for p in evaluation]
    outcomes = [p.actual_home_outcome for p in evaluation]
    _H2H_CACHE = _bucket_predictions(probs, outcomes, n_bins=20)
    return _H2H_CACHE


def h2h_calibration_band(db: Session, model_probability: float) -> CalibrationBand | None:
    rows = _get_h2h_raw_predictions(db)
    if not rows:
        return None
    row = _band_containing(rows, model_probability)
    if row is None:
        return None
    return CalibrationBand(
        band_label=f'{row["lo"]:.0%}-{row["hi"]:.0%}',
        avg_predicted=row["avg_predicted"],
        actual_rate=row["actual_rate"],
        n=row["n"],
        meets_min_sample=row["n"] >= MIN_H2H_BAND_SAMPLE,
    )


def _disposal_band_rows(db: Session, nearest_threshold: float) -> list[dict]:
    if nearest_threshold in _disposal_band_cache:
        return _disposal_band_cache[nearest_threshold]
    run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.market == "player_disposals", PlayerModelRun.is_promoted.is_(True)))
    if run is None:
        _disposal_band_cache[nearest_threshold] = []
        return []
    preds = db.scalars(select(PlayerDisposalPrediction).where(PlayerDisposalPrediction.model_run_id == run.id)).all()
    probs, outcomes = [], []
    for p in preds:
        prob = price_line(disposal_distribution_for(p), nearest_threshold, "multi_plus")
        probs.append(prob)
        outcomes.append(1.0 if p.actual_disposals >= nearest_threshold else 0.0)
    rows = _bucket_predictions(probs, outcomes, n_bins=10)
    _disposal_band_cache[nearest_threshold] = rows
    return rows


def disposal_calibration_band(db: Session, threshold: float, model_probability: float) -> CalibrationBand | None:
    nearest = min(_DISPOSAL_CALIBRATION_THRESHOLDS, key=lambda t: abs(t - threshold))
    rows = _disposal_band_rows(db, float(nearest))
    if not rows:
        return None
    row = _band_containing(rows, model_probability)
    if row is None:
        return None
    return CalibrationBand(
        band_label=f'{row["lo"]:.0%}-{row["hi"]:.0%}',
        avg_predicted=row["avg_predicted"],
        actual_rate=row["actual_rate"],
        n=row["n"],
        meets_min_sample=row["n"] >= MIN_DISPOSAL_BAND_SAMPLE,
    )


def _goal_band_rows(db: Session, nearest_threshold: float) -> list[dict]:
    if nearest_threshold in _goal_band_cache:
        return _goal_band_cache[nearest_threshold]
    run = db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
    if run is None:
        _goal_band_cache[nearest_threshold] = []
        return []
    preds = db.scalars(select(PlayerGoalPrediction).where(PlayerGoalPrediction.model_run_id == run.id)).all()
    probs, outcomes = [], []
    for p in preds:
        prob = price_line(goal_distribution_for(p), nearest_threshold, "multi_plus")
        probs.append(prob)
        outcomes.append(1.0 if p.actual_goals >= nearest_threshold else 0.0)
    rows = _bucket_predictions(probs, outcomes, n_bins=10)
    _goal_band_cache[nearest_threshold] = rows
    return rows


def goal_calibration_band(db: Session, threshold: float, model_probability: float) -> CalibrationBand | None:
    nearest = min(_GOAL_CALIBRATION_THRESHOLDS, key=lambda t: abs(t - threshold))
    rows = _goal_band_rows(db, float(nearest))
    if not rows:
        return None
    row = _band_containing(rows, model_probability)
    if row is None:
        return None
    return CalibrationBand(
        band_label=f'{row["lo"]:.0%}-{row["hi"]:.0%}',
        avg_predicted=row["avg_predicted"],
        actual_rate=row["actual_rate"],
        n=row["n"],
        meets_min_sample=row["n"] >= MIN_GOAL_BAND_SAMPLE,
    )


def calibration_band_for_opportunity(db: Session, opportunity: dict) -> CalibrationBand | None:
    market_type = opportunity["market_type"]
    if market_type == "h2h":
        return h2h_calibration_band(db, opportunity["model_probability"])
    if market_type == "player_disposals":
        return disposal_calibration_band(db, opportunity["threshold"], opportunity["model_probability"])
    if market_type == "player_goals":
        return goal_calibration_band(db, opportunity["threshold"], opportunity["model_probability"])
    return None  # line/total are point-estimate markets, not probability-threshold ones - no probability band applies


def calibration_band_as_dict(b: CalibrationBand) -> dict:
    return {
        "band_label": b.band_label,
        "avg_predicted": b.avg_predicted,
        "actual_rate": b.actual_rate,
        "n": b.n,
        "meets_min_sample": b.meets_min_sample,
    }
