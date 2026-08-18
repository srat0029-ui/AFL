"""Model-strength context (Weekly Bet Review + Decision Support stage,
Section 3) — the ACTUAL historical quality of the model behind each market
type, never the same generic confidence text reused everywhere. Every
number here comes from an already-run evaluation (Elo/Poisson's rigorous
walk-forward backtest in app/backtesting/evaluation.py, or the
already-persisted disposal/goal validation metrics from the original
2016-2025 backtests) — nothing is retrained or retuned here.

Elo/Poisson's evaluation report recomputes a full walk-forward backtest
(documented elsewhere in this codebase as taking 3-33s), so it is cached
at process level exactly like app/api/routes/backtests.py already caches
its own expensive comparison endpoints — recomputed once per process
lifetime, invalidated only by a server restart after a real CLI retrain.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtesting.evaluation import ModelsUnavailableError, load_elo_evaluation, load_poisson_evaluation
from app.models import GoalModelRun, GoalModelValidationMetric, PlayerModelRun, PlayerModelValidationMetric
from app.player_modelling.live_report_query import _DISPOSAL_CALIBRATION_THRESHOLDS, _GOAL_CALIBRATION_THRESHOLDS

MIN_DISPOSAL_CALIBRATION_SAMPLE = 1000
MIN_GOAL_CALIBRATION_SAMPLE = 500  # goals are much rarer events than disposal thresholds - a smaller real sample is expected, but still gated

_elo_cache: object | None = None
_elo_cache_attempted = False
_poisson_cache: tuple | None = None
_poisson_cache_attempted = False


def _get_elo_report(db: Session):
    global _elo_cache, _elo_cache_attempted
    if not _elo_cache_attempted:
        _elo_cache_attempted = True
        try:
            _run, report = load_elo_evaluation(db)
            _elo_cache = report
        except ModelsUnavailableError:
            _elo_cache = None
    return _elo_cache


def _get_poisson_reports(db: Session):
    global _poisson_cache, _poisson_cache_attempted
    if not _poisson_cache_attempted:
        _poisson_cache_attempted = True
        try:
            _run, win_report, scoring_report = load_poisson_evaluation(db)
            _poisson_cache = (win_report, scoring_report)
        except ModelsUnavailableError:
            _poisson_cache = None
    return _poisson_cache


@dataclass(frozen=True)
class ModelStrengthContext:
    market_type: str
    model_name: str
    metrics: dict  # market-type-specific keys - see each builder function
    evaluation_sample: int
    caveats: list[str]


def h2h_model_strength(db: Session) -> ModelStrengthContext | None:
    report = _get_elo_report(db)
    if report is None:
        return None
    m = report.evaluation_metrics
    return ModelStrengthContext(
        market_type="h2h",
        model_name="Elo",
        metrics={
            "brier_score": m.get("brier_score"),
            "accuracy": m.get("accuracy"),
            "calibration_ece": report.calibration_ece,
        },
        evaluation_sample=report.period.n_evaluation,
        caveats=[],
    )


def line_or_total_model_strength(db: Session, market_type: str) -> ModelStrengthContext | None:
    if market_type not in ("line", "total"):
        return None
    cached = _get_poisson_reports(db)
    if cached is None:
        return None
    _win_report, scoring_report = cached
    m = scoring_report.evaluation_metrics
    key_prefix = "margin" if market_type == "line" else "total_points"
    return ModelStrengthContext(
        market_type=market_type,
        model_name="Poisson (revised)",
        metrics={
            "mae": m.get(f"{key_prefix}_mae"),
            "rmse": m.get(f"{key_prefix}_rmse"),
            "bias": m.get(f"{key_prefix}_bias"),
        },
        evaluation_sample=scoring_report.period.n_evaluation,
        caveats=[],
    )


def _metric_row(db: Session, metric_cls, run_id: int, segment: str, metric_name: str):
    return db.scalar(
        select(metric_cls).where(metric_cls.model_run_id == run_id, metric_cls.segment == segment, metric_cls.metric_name == metric_name)
    )


def disposal_model_strength(db: Session, threshold: float) -> ModelStrengthContext | None:
    run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.market == "player_disposals", PlayerModelRun.is_promoted.is_(True)))
    if run is None:
        return None
    mae_row = _metric_row(db, PlayerModelValidationMetric, run.id, "overall", "mae")
    nearest = min(_DISPOSAL_CALIBRATION_THRESHOLDS, key=lambda t: abs(t - threshold))
    segment = f"threshold_{nearest}"
    brier_row = _metric_row(db, PlayerModelValidationMetric, run.id, segment, "brier")
    ece_row = _metric_row(db, PlayerModelValidationMetric, run.id, segment, "ece")

    caveats = []
    n = ece_row.n if ece_row is not None else (brier_row.n if brier_row is not None else 0)
    if n < MIN_DISPOSAL_CALIBRATION_SAMPLE:
        caveats.append(f"Evaluation sample for the {nearest}+ threshold ({n:,}) is below the reliable-calibration bar.")
    if nearest != threshold:
        caveats.append(f"Showing metrics for the nearest evaluated threshold ({nearest}+), not the exact line ({threshold:g}).")

    return ModelStrengthContext(
        market_type="player_disposals",
        model_name=run.model_name,
        metrics={
            "overall_mae": mae_row.value if mae_row is not None else None,
            "threshold_evaluated": float(nearest),
            "threshold_brier": brier_row.value if brier_row is not None else None,
            "threshold_ece": ece_row.value if ece_row is not None else None,
        },
        evaluation_sample=n,
        caveats=caveats,
    )


def goal_model_strength(db: Session, threshold: float) -> ModelStrengthContext | None:
    run = db.scalar(select(GoalModelRun).where(GoalModelRun.is_promoted.is_(True)))
    if run is None:
        return None
    nearest = min(_GOAL_CALIBRATION_THRESHOLDS, key=lambda t: abs(t - threshold))
    segment = f"threshold_{nearest}"
    brier_row = _metric_row(db, GoalModelValidationMetric, run.id, segment, "brier")
    ece_row = _metric_row(db, GoalModelValidationMetric, run.id, segment, "ece")

    n = ece_row.n if ece_row is not None else (brier_row.n if brier_row is not None else 0)
    caveats = []
    if n < MIN_GOAL_CALIBRATION_SAMPLE:
        caveats.append(f"Evaluation sample for the {nearest}+ threshold ({n:,}) is small — goal thresholds are rarer events than disposal thresholds.")
    if nearest != threshold:
        caveats.append(f"Showing metrics for the nearest evaluated threshold ({nearest}+), not the exact line ({threshold:g}).")

    return ModelStrengthContext(
        market_type="player_goals",
        model_name=run.model_name,
        metrics={
            "threshold_evaluated": float(nearest),
            "threshold_brier": brier_row.value if brier_row is not None else None,
            "threshold_ece": ece_row.value if ece_row is not None else None,
        },
        evaluation_sample=n,
        caveats=caveats,
    )


def model_strength_for_opportunity(db: Session, opportunity: dict) -> ModelStrengthContext | None:
    """Dispatches on the opportunity's own market_type - the one entry
    point callers (the Weekly Review comparison table, the deep-audit
    report) should use, so market-type dispatch logic lives in exactly
    one place."""
    market_type = opportunity["market_type"]
    if market_type == "h2h":
        return h2h_model_strength(db)
    if market_type in ("line", "total"):
        return line_or_total_model_strength(db, market_type)
    if market_type == "player_disposals":
        return disposal_model_strength(db, opportunity["threshold"])
    if market_type == "player_goals":
        return goal_model_strength(db, opportunity["threshold"])
    return None


def model_strength_as_dict(m: ModelStrengthContext) -> dict:
    return {
        "market_type": m.market_type,
        "model_name": m.model_name,
        "metrics": m.metrics,
        "evaluation_sample": m.evaluation_sample,
        "caveats": m.caveats,
    }
