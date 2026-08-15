"""CLI to run and persist the gradient-boosting match-winner evaluation —
XGBoost vs LightGBM across five controlled feature sets, calibration,
bootstrap uncertainty vs Elo, feature importance, interactions, and a
simple Elo+boosting ensemble experiment.

Usage:
    python -m app.modelling.boosting_cli

Persists the winning (library, feature-set) candidate's config to ModelRun
(model_name "boosting") for reproducibility/documentation only — the live
dashboard is unaffected (see app/edges/calculator.py, untouched by this
stage). See app/backtesting/boosting_report.py for the full pipeline this
just drives and prints.
"""

import sys
import warnings
from dataclasses import asdict, dataclass

from app.backtesting.boosting_report import build_boosting_comparison
from app.backtesting.evaluation import EVALUATION_START_YEAR
from app.database import SessionLocal
from app.modelling.model_run_persistence import persist_model_run
from app.models import ModelRun
from sqlalchemy import select

warnings.filterwarnings("ignore", category=FutureWarning)


@dataclass(frozen=True)
class PersistedBoostingConfig:
    library: str
    feature_set_label: str
    feature_names: tuple[str, ...]
    hyperparameters: dict
    calibration_method: str
    evaluation_start_year: int
    ensemble_boosting_weight: float
    ensemble_recommended: bool


def main(argv: list[str] | None = None) -> int:
    db = SessionLocal()
    try:
        elo_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "elo"))
        poisson_run = db.scalar(select(ModelRun).where(ModelRun.model_name == "poisson"))
        if elo_run is None or poisson_run is None:
            print("Run `python -m app.modelling.elo_cli` and `python -m app.modelling.poisson_cli` first.")
            return 1

        print("Tuning XGBoost and LightGBM, evaluating 5 feature sets each, running the full analysis "
              "on the best candidate (calibration, bootstrap, ablation, SHAP interactions, ensemble)...")
        overview = build_boosting_comparison(db)

        print(f"\n=== Evaluation period {overview.evaluation_start_year}-{overview.evaluation_end_year}, n={overview.n_eval} ===")
        print("\nAll (library, feature set) candidates, raw (uncalibrated) probabilities:")
        for c in sorted(overview.feature_set_candidates, key=lambda c: c.brier_score):
            marker = "  <-- best" if c.label == overview.best.label and c.library == overview.best.library else ""
            print(f"  {c.library:9s} {c.label:30s} brier={c.brier_score:.4f}  log_loss={c.log_loss:.4f}  accuracy={c.accuracy:.1%}{marker}")

        b = overview.best
        print(f"\nBest candidate: {b.library} / {b.label}")
        print(f"  hyperparameters: {b.hyperparameters}")
        print(f"  calibration method selected: {b.calibration_method}")
        print(
            f"  final (calibrated) metrics: brier={b.brier_score:.4f}  log_loss={b.log_loss:.4f}  "
            f"accuracy={b.accuracy:.1%}  ECE={b.calibration_ece:.4f}"
        )
        bs = b.bootstrap_vs_elo
        print(f"  Brier improvement vs Elo: {bs.point_estimate:+.4f}  95% bootstrap interval: [{bs.ci_low:+.4f}, {bs.ci_high:+.4f}]")
        print(f"  Promotion: {'PROMOTE' if b.promotion.promote else 'KEEP ELO'}")
        for reason in b.promotion.reasons:
            print(f"    {reason}")

        print("\nFeature-group ablation (Brier, vs Elo alone):")
        for a in b.feature_group_ablation:
            print(f"    {a.label:<28} brier={a.brier_score:.4f}  vs_elo={a.brier_vs_elo_alone:+.4f}")

        print("\nPermutation importance (top 5):")
        for name, val in sorted(b.permutation_importance.items(), key=lambda kv: kv[1], reverse=True)[:5]:
            print(f"    {name:<40} {val:+.5f}")

        if b.shap_importance:
            print("\nSHAP mean |contribution| (top 5):")
            for name, val in sorted(b.shap_importance.items(), key=lambda kv: kv[1], reverse=True)[:5]:
                print(f"    {name:<40} {val:.5f}")

        if b.interactions:
            print("\nTop SHAP interactions:")
            for i in b.interactions[:6]:
                print(f"    {i.label:<70} mean|interaction|={i.mean_abs_interaction:.5f}")
        else:
            print("\nNo interaction data (best candidate uses LightGBM, which has no native interaction API).")

        e = overview.ensemble
        print(f"\nEnsemble: boosting_weight={e.boosting_weight}")
        print(f"  Elo alone:      brier={e.elo.brier_score:.4f}")
        print(f"  Boosting alone: brier={e.boosting.brier_score:.4f}")
        print(f"  Ensemble:       brier={e.ensemble.brier_score:.4f}")
        print(f"  Ensemble vs Elo bootstrap:      {e.bootstrap_ensemble_vs_elo}")
        print(f"  Ensemble vs boosting bootstrap: {e.bootstrap_ensemble_vs_boosting}")
        print(f"  Use ensemble: {e.use_ensemble}")

        config = PersistedBoostingConfig(
            library=b.library, feature_set_label=b.label, feature_names=b.feature_names,
            hyperparameters=b.hyperparameters, calibration_method=b.calibration_method,
            evaluation_start_year=EVALUATION_START_YEAR,
            ensemble_boosting_weight=e.boosting_weight, ensemble_recommended=e.use_ensemble,
        )
        persist_model_run(
            db, model_name="boosting", config=config, tune_end_year=EVALUATION_START_YEAR - 1,
            metrics=[
                {
                    "market_type": "h2h", "metric_name": "brier_score", "holdout_n": b.n_eval,
                    "holdout_value": b.brier_score, "naive_baseline_value": overview.ensemble.elo.brier_score,
                    "has_edge_over_naive": b.brier_score < overview.ensemble.elo.brier_score,
                }
            ],
        )
        print("\nPersisted boosting config (for reproducibility — the live dashboard is unaffected).")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
