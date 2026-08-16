"""Reads the persisted disposal model runs/metrics/predictions (see
app/player_modelling/disposal_persistence.py) for the API layer -
deliberately NOT re-running the backtest per request (that takes on the
order of a minute for the full analysis suite; the whole point of
persisting predictions is to make this instant and reproducible). Mirrors
app/backtesting/evaluation.py's "load_*" naming for the team-level
equivalent.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, PlayerDisposalPrediction, PlayerModelRun, PlayerModelValidationMetric
from app.player_modelling.disposal_confidence import ConfidenceInputs, ConfidenceTier, classify_confidence
from app.player_modelling.disposal_distribution import NegativeBinomialDistribution


class ModelsUnavailableError(Exception):
    """Raised when no disposal model run has been persisted yet (the CLI
    hasn't been run) - the router turns this into a 503, not a 500."""


def _metrics_dict(db: Session, run_id: int) -> dict[tuple[str, str], PlayerModelValidationMetric]:
    rows = db.scalars(select(PlayerModelValidationMetric).where(PlayerModelValidationMetric.model_run_id == run_id)).all()
    return {(m.segment, m.metric_name): m for m in rows}


def load_promoted_run(db: Session) -> PlayerModelRun:
    run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.is_promoted.is_(True)))
    if run is None:
        raise ModelsUnavailableError("No promoted disposal model run found - run `python -m app.player_modelling.disposal_cli` first.")
    return run


def load_all_runs(db: Session) -> list[PlayerModelRun]:
    return list(db.scalars(select(PlayerModelRun).order_by(PlayerModelRun.model_name)).all())


def run_summary(db: Session, run: PlayerModelRun) -> dict:
    metrics = _metrics_dict(db, run.id)
    mae = metrics.get(("overall", "mae"))
    rmse = metrics.get(("overall", "rmse"))
    bias = metrics.get(("overall", "bias"))
    return {
        "model_name": run.model_name,
        "market": run.market,
        "is_promoted": run.is_promoted,
        "distribution_method": run.distribution_method,
        "feature_names": run.feature_names,
        "tune_start_year": run.tune_start_year,
        "tune_end_year": run.tune_end_year,
        "evaluation_start_year": run.evaluation_start_year,
        "evaluation_end_year": run.evaluation_end_year,
        "run_at": run.run_at,
        "overall_mae": mae.value if mae else None,
        "overall_rmse": rmse.value if rmse else None,
        "overall_bias": bias.value if bias else None,
        "evaluation_n": mae.n if mae else None,
    }


@dataclass(frozen=True)
class BacktestSummary:
    promoted: dict
    baselines: list[dict]
    candidates: list[dict]
    season_breakdown: list[dict]
    within_2: float
    within_5: float
    within_10: float
    median_ae: float


def load_backtest_summary(db: Session) -> BacktestSummary:
    promoted_run = load_promoted_run(db)
    all_runs = load_all_runs(db)
    promoted_metrics = _metrics_dict(db, promoted_run.id)

    def _overall(run: PlayerModelRun) -> dict:
        m = _metrics_dict(db, run.id)
        mae = m.get(("overall", "mae"))
        rmse = m.get(("overall", "rmse"))
        bias = m.get(("overall", "bias"))
        return {
            "model_name": run.model_name,
            "mae": mae.value if mae else None,
            "rmse": rmse.value if rmse else None,
            "bias": bias.value if bias else None,
        }

    baselines = [_overall(r) for r in all_runs if r.model_name.startswith("disposals_baseline_")]
    candidates = [_overall(r) for r in all_runs if not r.model_name.startswith("disposals_baseline_")]

    season_breakdown = []
    for (segment, metric_name), m in promoted_metrics.items():
        if segment.startswith("season_") and metric_name == "mae":
            year = int(segment.removeprefix("season_"))
            rmse_m = promoted_metrics.get((segment, "rmse"))
            bias_m = promoted_metrics.get((segment, "bias"))
            season_breakdown.append(
                {"season_year": year, "n": m.n, "mae": m.value, "rmse": rmse_m.value if rmse_m else None, "bias": bias_m.value if bias_m else None}
            )
    season_breakdown.sort(key=lambda r: r["season_year"])

    return BacktestSummary(
        promoted=run_summary(db, promoted_run),
        baselines=baselines,
        candidates=candidates,
        season_breakdown=season_breakdown,
        within_2=promoted_metrics[("overall", "within_2")].value,
        within_5=promoted_metrics[("overall", "within_5")].value,
        within_10=promoted_metrics[("overall", "within_10")].value,
        median_ae=promoted_metrics[("overall", "median_ae")].value,
    )


def load_calibration_report(db: Session, model_name: str | None = None) -> dict:
    run = load_promoted_run(db) if model_name is None else db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == model_name))
    if run is None:
        raise ModelsUnavailableError(f"No disposal model run named {model_name!r}")
    metrics = _metrics_dict(db, run.id)

    thresholds = []
    for t in (20, 25, 30, 35):
        segment = f"threshold_{t}"
        brier = metrics.get((segment, "brier"))
        log_loss = metrics.get((segment, "log_loss"))
        ece = metrics.get((segment, "ece"))
        if brier is None:
            continue
        thresholds.append(
            {
                "threshold": float(t),
                "n": brier.n,
                "brier": brier.value,
                "log_loss": log_loss.value if log_loss else float("nan"),
                "ece": ece.value if ece else None,
                "calibration": [],  # reliability tables are not persisted per-bucket - reconstructing from raw predictions is deferred to a future stage
            }
        )

    intervals = []
    for c in (0.5, 0.8, 0.9):
        segment = f"interval_{int(c*100)}"
        coverage = metrics.get((segment, "coverage"))
        width = metrics.get((segment, "width"))
        if coverage is None:
            continue
        intervals.append({"coverage_target": c, "n": coverage.n, "empirical_coverage": coverage.value, "mean_width": width.value if width else float("nan")})

    return {"model_name": run.model_name, "distribution_method": run.distribution_method, "thresholds": thresholds, "intervals": intervals}


def load_player_predictions(db: Session, player_id: int, model_name: str | None = None) -> dict:
    player = db.get(Player, player_id)
    if player is None:
        raise ModelsUnavailableError(f"No player with id {player_id}")

    run = load_promoted_run(db) if model_name is None else db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == model_name))
    if run is None:
        raise ModelsUnavailableError(f"No disposal model run named {model_name!r}")

    rows = db.scalars(
        select(PlayerDisposalPrediction)
        .where(PlayerDisposalPrediction.model_run_id == run.id, PlayerDisposalPrediction.player_id == player_id)
        .order_by(PlayerDisposalPrediction.season_year)
    ).all()

    # league-wide bottom-quartile TOG isn't stored per-prediction; a fixed,
    # reasonable reference value (see disposal_confidence.py's docstring for
    # the real audited figure this approximates) avoids a second full table
    # scan just to serve this endpoint quickly.
    league_low_tog_cutoff = 60.0

    predictions = []
    for r in rows:
        dist = NegativeBinomialDistribution(mu=r.predicted_mean, alpha=r.nb_alpha)
        tier = classify_confidence(
            ConfidenceInputs(
                games_of_history=r.games_of_history,
                tog_last5_avg=None,  # not persisted per-row; history-based tiering still applies
                disposals_last5_std=None,
                league_low_tog_cutoff=league_low_tog_cutoff,
            )
        )
        predictions.append(
            {
                "match_id": r.match_id,
                "season_year": r.season_year,
                "games_of_history": r.games_of_history,
                "predicted_mean": r.predicted_mean,
                "actual_disposals": r.actual_disposals,
                "confidence_tier": tier.value,
                "interval_50": dist.interval(0.5),
                "interval_80": dist.interval(0.8),
                "prob_20_plus": dist.prob_at_least(20),
                "prob_25_plus": dist.prob_at_least(25),
                "prob_30_plus": dist.prob_at_least(30),
                "prob_35_plus": dist.prob_at_least(35),
            }
        )

    return {"player": player, "model_name": run.model_name, "predictions": predictions}
