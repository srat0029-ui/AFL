"""Model Registry + Prospective Live Evaluation API (/api/v1/model-registry).

Two strictly separate datasets, each carrying its own explicit
`dataset_label` so a consumer can never accidentally blend them:
  - GET /api/v1/model-registry — "Historical backtest": every persisted
    model run (champion/previous champion/rejected), the disposal
    Ridge-vs-Huber head-to-head, and the append-only promotion audit trail.
  - GET /api/v1/model-registry/prospective-evaluation — "Prospective live
    evaluation": model-vs-market performance from PricingSnapshot, the
    genuinely out-of-sample dataset. Honestly reports an accumulating-data
    state when nothing has settled yet, rather than an empty/misleading chart.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.pricing_schemas import (
    DisposalHeadToHeadRead,
    ModelRegistryRead,
    ModelRunSummaryRead,
    ProspectiveEvaluationRead,
    ProspectiveSplitRead,
    PromotionEventRead,
    SgmProspectiveEvaluationRead,
    SgmProspectiveSplitRead,
)
from app.database import get_db
from app.player_modelling.model_registry import (
    ModelRunSummary,
    disposal_ridge_vs_huber,
    list_disposal_models,
    list_goal_models,
    list_promotion_events,
    list_team_models,
)
from app.player_modelling.prospective_evaluation import ProspectiveSplit, load_prospective_evaluation
from app.player_modelling.sgm_prospective_evaluation import SgmProspectiveSplit, load_sgm_prospective_evaluation

router = APIRouter(prefix="/api/v1/model-registry", tags=["model-registry-v1"])


def _run_read(s: ModelRunSummary) -> ModelRunSummaryRead:
    return ModelRunSummaryRead(
        model_name=s.model_name, model_version=s.model_version, market=s.market, status=s.status, run_at=s.run_at,
        tune_start_year=s.tune_start_year, tune_end_year=s.tune_end_year, evaluation_start_year=s.evaluation_start_year,
        evaluation_end_year=s.evaluation_end_year, sample_size=s.sample_size, point_metrics=s.point_metrics,
        calibration_metrics=s.calibration_metrics, promotion_reason=s.promotion_reason,
    )


@router.get("", response_model=ModelRegistryRead)
def get_model_registry(db: Session = Depends(get_db)) -> ModelRegistryRead:
    h2h = disposal_ridge_vs_huber(db)
    return ModelRegistryRead(
        disposal_models=[_run_read(s) for s in list_disposal_models(db)],
        goal_models=[_run_read(s) for s in list_goal_models(db)],
        team_models=[_run_read(s) for s in list_team_models(db)],
        disposal_head_to_head=DisposalHeadToHeadRead(
            ridge=_run_read(h2h.ridge) if h2h.ridge else None, huber=_run_read(h2h.huber) if h2h.huber else None,
            ridge_high_volume_bias=h2h.ridge_high_volume_bias, huber_high_volume_bias=h2h.huber_high_volume_bias,
            ridge_low_history_bias=h2h.ridge_low_history_bias, huber_low_history_bias=h2h.huber_low_history_bias,
        ),
        promotion_events=[
            PromotionEventRead(
                market=e.market, previous_champion_model_name=e.previous_champion_model_name,
                previous_champion_model_version=e.previous_champion_model_version,
                new_champion_model_name=e.new_champion_model_name, new_champion_model_version=e.new_champion_model_version,
                promoted_at=e.promoted_at, evidence_summary=e.evidence_summary, evaluation_metrics=e.evaluation_metrics,
            )
            for e in list_promotion_events(db)
        ],
    )


def _split_read(s: ProspectiveSplit) -> ProspectiveSplitRead:
    return ProspectiveSplitRead(
        label=s.label, n_settled=s.n_settled, n_unique_events=s.n_unique_events, model_brier=s.model_brier,
        market_brier=s.market_brier, model_log_loss=s.model_log_loss, market_log_loss=s.market_log_loss,
        model_calibration_ece=s.model_calibration_ece, n_with_market_consensus=s.n_with_market_consensus,
        exploratory=s.exploratory,
    )


@router.get("/prospective-evaluation", response_model=ProspectiveEvaluationRead)
def get_prospective_evaluation(db: Session = Depends(get_db)) -> ProspectiveEvaluationRead:
    report = load_prospective_evaluation(db)
    return ProspectiveEvaluationRead(
        has_settled_data=report.has_settled_data, n_frozen_total=report.n_frozen_total, n_settled=report.n_settled,
        n_unique_player_match_events=report.n_unique_player_match_events,
        overall=_split_read(report.overall) if report.overall else None,
        by_market_family=[_split_read(s) for s in report.by_market_family],
        by_probability_bucket=[_split_read(s) for s in report.by_probability_bucket],
        by_model_version=[_split_read(s) for s in report.by_model_version],
        message=report.message,
    )


def _sgm_split_read(s: SgmProspectiveSplit) -> SgmProspectiveSplitRead:
    return SgmProspectiveSplitRead(
        label=s.label, n_settled=s.n_settled, n_unique_combos=s.n_unique_combos, model_brier=s.model_brier,
        naive_brier=s.naive_brier, model_log_loss=s.model_log_loss, naive_log_loss=s.naive_log_loss,
        model_calibration_ece=s.model_calibration_ece, bookmaker_brier=s.bookmaker_brier,
        bookmaker_log_loss=s.bookmaker_log_loss, n_with_bookmaker_price=s.n_with_bookmaker_price, exploratory=s.exploratory,
    )


@router.get("/sgm-prospective-evaluation", response_model=SgmProspectiveEvaluationRead)
def get_sgm_prospective_evaluation(db: Session = Depends(get_db)) -> SgmProspectiveEvaluationRead:
    """Separate dataset and endpoint from /prospective-evaluation above -
    Same Game Multi joint prices are multi-leg and evaluated against naive
    independence (and a genuine bookmaker SGM price, when one exists - see
    sgm_prospective_evaluation.py's module docstring for why that's always
    empty today), not against market consensus."""
    report = load_sgm_prospective_evaluation(db)
    return SgmProspectiveEvaluationRead(
        has_settled_data=report.has_settled_data, n_frozen_total=report.n_frozen_total, n_settled=report.n_settled,
        n_unique_combos=report.n_unique_combos,
        overall=_sgm_split_read(report.overall) if report.overall else None,
        by_n_legs=[_sgm_split_read(s) for s in report.by_n_legs],
        by_leg_combination=[_sgm_split_read(s) for s in report.by_leg_combination],
        by_correlation_adjustment_magnitude=[_sgm_split_read(s) for s in report.by_correlation_adjustment_magnitude],
        by_snapshot_horizon=[_sgm_split_read(s) for s in report.by_snapshot_horizon],
        message=report.message,
    )
