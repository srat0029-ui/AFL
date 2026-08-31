"""Model Registry (read-only) — lists every persisted model run per market
family with a commercially-legible status, plus the append-only promotion
audit trail. Reads only already-persisted PlayerModelRun/GoalModelRun/
ModelRun + validation metrics; computes nothing new, retrains nothing.

Status classification is derived entirely from existing data, never
guessed:
  - Champion: is_promoted=True right now.
  - Previous Champion: named as the previous_champion in the MOST RECENT
    ModelPromotionEvent for this market (so a champion two promotions ago
    reads as "Rejected", not "Previous Champion" — only the most recent
    hand-off is "previous").
  - Rejected: every other persisted, non-promoted run for this market —
    it was fit, evaluated, and not selected.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    GoalModelRun,
    GoalModelValidationMetric,
    ModelPromotionEvent,
    ModelRun,
    PlayerModelRun,
    PlayerModelValidationMetric,
)

STATUS_CHAMPION = "champion"
STATUS_PREVIOUS_CHAMPION = "previous_champion"
STATUS_CHALLENGER = "challenger"
STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class ModelRunSummary:
    model_name: str
    model_version: str
    market: str
    status: str
    run_at: datetime
    tune_start_year: int
    tune_end_year: int
    evaluation_start_year: int
    evaluation_end_year: int
    sample_size: int | None
    point_metrics: dict  # mae/rmse/bias where relevant
    calibration_metrics: dict  # brier/ece by threshold where relevant
    promotion_reason: str | None


def _latest_promotion_event(db: Session, market: str) -> ModelPromotionEvent | None:
    return db.scalar(
        select(ModelPromotionEvent).where(ModelPromotionEvent.market == market).order_by(ModelPromotionEvent.promoted_at.desc())
    )


def _status_for(run_model_name: str, is_promoted: bool, latest_event: ModelPromotionEvent | None) -> str:
    if is_promoted:
        return STATUS_CHAMPION
    if latest_event is not None and latest_event.previous_champion_model_name == run_model_name:
        return STATUS_PREVIOUS_CHAMPION
    return STATUS_REJECTED


def _metric_value(db: Session, metric_cls, run_id: int, segment: str, metric_name: str) -> float | None:
    row = db.scalar(select(metric_cls).where(metric_cls.model_run_id == run_id, metric_cls.segment == segment, metric_cls.metric_name == metric_name))
    return row.value if row is not None else None


def _metric_n(db: Session, metric_cls, run_id: int, segment: str, metric_name: str) -> int | None:
    row = db.scalar(select(metric_cls).where(metric_cls.model_run_id == run_id, metric_cls.segment == segment, metric_cls.metric_name == metric_name))
    return row.n if row is not None else None


def list_disposal_models(db: Session) -> list[ModelRunSummary]:
    runs = db.scalars(select(PlayerModelRun).where(PlayerModelRun.market == "player_disposals").order_by(PlayerModelRun.run_at)).all()
    latest_event = _latest_promotion_event(db, "player_disposals")
    summaries = []
    for run in runs:
        status = _status_for(run.model_name, run.is_promoted, latest_event)
        point = {
            "mae": _metric_value(db, PlayerModelValidationMetric, run.id, "overall", "mae"),
            "rmse": _metric_value(db, PlayerModelValidationMetric, run.id, "overall", "rmse"),
            "bias": _metric_value(db, PlayerModelValidationMetric, run.id, "overall", "bias"),
        }
        calibration = {}
        for t in (20, 25, 30, 35):
            seg = f"threshold_{t}"
            brier = _metric_value(db, PlayerModelValidationMetric, run.id, seg, "brier")
            ece = _metric_value(db, PlayerModelValidationMetric, run.id, seg, "ece")
            if brier is not None or ece is not None:
                calibration[f"{t}+"] = {"brier": brier, "ece": ece}
        n = _metric_n(db, PlayerModelValidationMetric, run.id, "overall", "mae")
        reason = None
        if status == STATUS_CHAMPION and latest_event is not None and latest_event.new_champion_model_name == run.model_name:
            reason = latest_event.evidence_summary
        summaries.append(ModelRunSummary(
            model_name=run.model_name, model_version=f"{run.model_name}@{run.run_at.isoformat()}", market=run.market,
            status=status, run_at=run.run_at, tune_start_year=run.tune_start_year, tune_end_year=run.tune_end_year,
            evaluation_start_year=run.evaluation_start_year, evaluation_end_year=run.evaluation_end_year,
            sample_size=n, point_metrics=point, calibration_metrics=calibration, promotion_reason=reason,
        ))
    return summaries


def list_goal_models(db: Session) -> list[ModelRunSummary]:
    runs = db.scalars(select(GoalModelRun).where(GoalModelRun.market == "player_goals").order_by(GoalModelRun.run_at)).all()
    latest_event = _latest_promotion_event(db, "player_goals")
    summaries = []
    for run in runs:
        status = _status_for(run.model_name, run.is_promoted, latest_event)
        point = {
            "mae": _metric_value(db, GoalModelValidationMetric, run.id, "overall", "mae"),
            "bias": _metric_value(db, GoalModelValidationMetric, run.id, "overall", "bias"),
        }
        calibration = {}
        for t in (1, 2, 3, 4, 5):
            seg = f"threshold_{t}"
            brier = _metric_value(db, GoalModelValidationMetric, run.id, seg, "brier")
            ece = _metric_value(db, GoalModelValidationMetric, run.id, seg, "ece")
            if brier is not None or ece is not None:
                calibration[f"{t}+"] = {"brier": brier, "ece": ece}
        n = _metric_n(db, GoalModelValidationMetric, run.id, "overall", "mae")
        reason = None
        if status == STATUS_CHAMPION and latest_event is not None and latest_event.new_champion_model_name == run.model_name:
            reason = latest_event.evidence_summary
        summaries.append(ModelRunSummary(
            model_name=run.model_name, model_version=f"{run.model_name}@{run.run_at.isoformat()}", market=run.market,
            status=status, run_at=run.run_at, tune_start_year=run.tune_start_year, tune_end_year=run.tune_end_year,
            evaluation_start_year=run.evaluation_start_year, evaluation_end_year=run.evaluation_end_year,
            sample_size=n, point_metrics=point, calibration_metrics=calibration, promotion_reason=reason,
        ))
    return summaries


_LIVE_TEAM_MODEL_NAMES = ("elo", "poisson")  # the only two app/edges/calculator.py actually reads live - see build_model_context


def list_team_models(db: Session) -> list[ModelRunSummary]:
    """Elo/Poisson are upserted in place (one row per model_name, no
    promotion concept — see app/modelling/model_run_persistence.py) and
    ARE the live models (app/edges/calculator.py reads exactly these two
    by name), so both read as Champion. Any other persisted ModelRun
    (logistic/boosting variants from offline research) never feeds a live
    prediction and reads as Rejected — labelling them Champion would be
    actively misleading to a commercial consumer of this registry."""
    runs = db.scalars(select(ModelRun)).all()
    summaries = []
    for run in runs:
        status = STATUS_CHAMPION if run.model_name in _LIVE_TEAM_MODEL_NAMES else STATUS_REJECTED
        summaries.append(ModelRunSummary(
            model_name=run.model_name, model_version=f"{run.model_name}@{run.run_at.isoformat()}", market="team",
            status=status, run_at=run.run_at, tune_start_year=run.tune_end_year, tune_end_year=run.tune_end_year,
            evaluation_start_year=run.tune_end_year + 1, evaluation_end_year=run.tune_end_year + 1,
            sample_size=None, point_metrics={}, calibration_metrics={}, promotion_reason=None,
        ))
    return summaries


@dataclass(frozen=True)
class PromotionEventSummary:
    market: str
    previous_champion_model_name: str | None
    previous_champion_model_version: str | None
    new_champion_model_name: str
    new_champion_model_version: str
    promoted_at: datetime
    evidence_summary: str
    evaluation_metrics: dict


def list_promotion_events(db: Session, market: str | None = None) -> list[PromotionEventSummary]:
    stmt = select(ModelPromotionEvent).order_by(ModelPromotionEvent.promoted_at.desc())
    if market is not None:
        stmt = stmt.where(ModelPromotionEvent.market == market)
    events = db.scalars(stmt).all()
    return [
        PromotionEventSummary(
            market=e.market, previous_champion_model_name=e.previous_champion_model_name,
            previous_champion_model_version=e.previous_champion_model_version,
            new_champion_model_name=e.new_champion_model_name, new_champion_model_version=e.new_champion_model_version,
            promoted_at=e.promoted_at, evidence_summary=e.evidence_summary, evaluation_metrics=e.evaluation_metrics,
        )
        for e in events
    ]


def record_promotion_event(
    db: Session, *, market: str, previous_champion_model_name: str | None, previous_champion_model_version: str | None,
    new_champion_model_name: str, new_champion_model_version: str, promoted_at: datetime, evidence_summary: str,
    evaluation_metrics: dict,
) -> ModelPromotionEvent:
    """Always INSERTs a new row - this table is append-only by design (see
    its model docstring); there is no update/upsert path here."""
    event = ModelPromotionEvent(
        market=market, previous_champion_model_name=previous_champion_model_name,
        previous_champion_model_version=previous_champion_model_version, new_champion_model_name=new_champion_model_name,
        new_champion_model_version=new_champion_model_version, promoted_at=promoted_at,
        evidence_summary=evidence_summary, evaluation_metrics=evaluation_metrics,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@dataclass(frozen=True)
class DisposalHeadToHead:
    ridge: ModelRunSummary | None
    huber: ModelRunSummary | None
    ridge_high_volume_bias: dict  # {"22+": x, "25+": x, "28+": x} — from the promotion evidence, not re-derived
    huber_high_volume_bias: dict
    ridge_low_history_bias: dict  # {"<5": x, "5-9": x, "10-19": x, "20+": x}
    huber_low_history_bias: dict


def disposal_ridge_vs_huber(db: Session) -> DisposalHeadToHead:
    """The specific side-by-side this stage asks for. High-volume/
    low-history bias figures are read from the promotion event's frozen
    evaluation_metrics JSON (captured once, at promotion time, from the
    actual challenger-research run) rather than re-derived here — this
    module never re-fits or re-evaluates a model."""
    models = {m.model_name: m for m in list_disposal_models(db)}
    ridge, huber = models.get("disposals_ridge"), models.get("disposals_huber")
    event = _latest_promotion_event(db, "player_disposals")
    ridge_hv, huber_hv, ridge_lh, huber_lh = {}, {}, {}, {}
    if event is not None:
        m = event.evaluation_metrics or {}
        ridge_hv, huber_hv = m.get("ridge_high_volume_bias", {}), m.get("huber_high_volume_bias", {})
        ridge_lh, huber_lh = m.get("ridge_low_history_bias", {}), m.get("huber_low_history_bias", {})
    return DisposalHeadToHead(
        ridge=ridge, huber=huber, ridge_high_volume_bias=ridge_hv, huber_high_volume_bias=huber_hv,
        ridge_low_history_bias=ridge_lh, huber_low_history_bias=huber_lh,
    )
