"""One-off backfill: records the Ridge->Huber promotion (already applied —
see scripts/promote_huber_disposal_model.py) into the append-only
ModelPromotionEvent audit trail, using the real evidence numbers already
produced by scripts/elite_disposal_challenger_research.py's output.

Run once: python -m scripts.record_huber_promotion_event
"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import PlayerModelRun
from app.player_modelling.model_registry import record_promotion_event

EVIDENCE_SUMMARY = (
    "Controlled OLS (n=62,173) found Ridge's elite/high-volume under-prediction survives "
    "controlling for sample size, season, and TOG - not a small-sample artefact. A Ridge alpha "
    "grid (0.1-50) showed elite bias barely moves with regularisation strength, ruling out "
    "excessive L2 shrinkage as the cause. Huber (robust loss) improved overall MAE (3.931->3.907) "
    "and bias (+0.063->-0.030), reduced 28+ bias (-0.420->-0.307) and 25+ bias (-0.065->+0.013), "
    "IMPROVED (not worsened) low-history (<5 games) over-prediction (+1.703->+1.082), maintained "
    "calibration at 20/25/30/35+, and won on MAE in all 8 evaluated seasons (2019-2026). "
    "ElasticNet and a player-baseline+contextual-residual challenger were both evaluated and "
    "rejected (worse elite bias and/or worse low-history bias) — see "
    "scripts/elite_disposal_challenger_research.py for the full comparison."
)

EVALUATION_METRICS = {
    "overall_mae": {"ridge": 3.931, "huber": 3.907},
    "overall_rmse": {"ridge": 5.073, "huber": 5.043},
    "overall_bias": {"ridge": 0.063, "huber": -0.030},
    "ridge_high_volume_bias": {"22+": -0.137, "25+": -0.065, "28+": -0.420},
    "huber_high_volume_bias": {"22+": -0.082, "25+": 0.013, "28+": -0.307},
    "ridge_low_history_bias": {"<5": 1.703, "5-9": 0.385, "10-19": 0.402, "20+": -0.068},
    "huber_low_history_bias": {"<5": 1.082, "5-9": 0.230, "10-19": 0.264, "20+": -0.127},
    "calibration_ece": {
        "ridge": {"20+": 0.0176, "25+": 0.0160, "30+": 0.0051, "35+": 0.0014},
        "huber": {"20+": 0.0202, "25+": 0.0155, "30+": 0.0042, "35+": 0.0019},
    },
    "interval_80_coverage": {"ridge": 0.810, "huber": 0.812},
    "seasons_huber_beat_ridge_on_mae": 8,
    "seasons_evaluated": 8,
    "challengers_rejected": ["elasticnet", "baseline_plus_contextual_residual"],
}


def main() -> int:
    db = SessionLocal()
    try:
        ridge = db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == "disposals_ridge"))
        huber = db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == "disposals_huber"))
        if huber is None:
            print("No disposals_huber run found — run scripts.promote_huber_disposal_model first.")
            return 1
        event = record_promotion_event(
            db, market="player_disposals",
            previous_champion_model_name=ridge.model_name if ridge else None,
            previous_champion_model_version=f"{ridge.model_name}@{ridge.run_at.isoformat()}" if ridge else None,
            new_champion_model_name=huber.model_name,
            new_champion_model_version=f"{huber.model_name}@{huber.run_at.isoformat()}",
            promoted_at=huber.run_at, evidence_summary=EVIDENCE_SUMMARY, evaluation_metrics=EVALUATION_METRICS,
        )
        print(f"Recorded promotion event id={event.id}: {event.previous_champion_model_name} -> {event.new_champion_model_name} at {event.promoted_at.isoformat()}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
