"""Promotes the validated Huber disposal model into the live pipeline
(Elite Disposal Model: Promotion stage), while preserving Ridge as a
non-promoted, fully reproducible model run for auditability/comparison.

What this does:
  1. Fits Huber on the exact same chronological tune split (pre-2019) as
     the promoted Ridge, using the SAME promoted feature set
     (PROMOTED_DISPOSAL_FEATURE_NAMES) - reusing disposal_backtest.py's
     standard candidate-model pipeline, not a one-off fit.
  2. Persists it as a NEW PlayerModelRun row named "disposals_huber",
     is_promoted=True - via the existing persist_model_run upsert, which
     only ever touches the row for THIS model_name.
  3. Explicitly flips the existing "disposals_ridge" row's is_promoted to
     False via a direct field update - deliberately NOT calling
     persist_model_run on it, since that would delete-and-replace its
     metrics/predictions (see disposal_persistence.py's docstring). Ridge's
     historical predictions/metrics are therefore left byte-for-byte
     unchanged; only its promotion flag moves.
  4. Verifies the invariant every downstream is_promoted=True lookup
     depends on: exactly one promoted disposal PlayerModelRun afterward.

Everything downstream of `is_promoted` (live projection generation,
calibration/confidence lookups, the Pricing API's model_version, the
Weekly Review / Match Centre / Player Insights model-strength displays)
is already keyed off whichever run has is_promoted=True and needs no
separate code change - see live_engine.py's/model_strength_context.py's
own docstrings for this being an explicit design goal ("promoting a
different model changes what project-upcoming fits next time,
automatically").

Does NOT regenerate live projections or pricing snapshots itself - see
scripts/regenerate_disposal_projections_and_compare.py for that (kept
separate so promotion and regeneration are each independently visible in
the run history/report).

Run: python -m scripts.promote_huber_disposal_model
"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import PlayerModelRun
from app.player_modelling.disposal_backtest import EVALUATION_START_YEAR, build_dataset, run_candidate_models
from app.player_modelling.disposal_evaluation import best_distribution_method, evaluate_model
from app.player_modelling.disposal_features import PROMOTED_DISPOSAL_FEATURE_NAMES
from app.player_modelling.disposal_persistence import persist_model_run

HUBER_MODEL_NAME = "disposals_huber"
RIDGE_MODEL_NAME = "disposals_ridge"


def main() -> int:
    db = SessionLocal()
    try:
        print("Building dataset (point-in-time features, full history)...")
        split = build_dataset(db)
        tune_start_year = min((r.season_year for r in split.tune_rows), default=EVALUATION_START_YEAR)
        tune_end_year = EVALUATION_START_YEAR - 1
        print(f"  tune rows: {len(split.tune_rows):,} (<{EVALUATION_START_YEAR})  eval rows: {len(split.eval_rows):,} (>={EVALUATION_START_YEAR})")

        print("Fitting Huber on the promoted feature set (chronological tune/eval - no live/future data used)...")
        preds = run_candidate_models(split, feature_names=PROMOTED_DISPOSAL_FEATURE_NAMES, model_names=("huber",))["huber"]
        method = best_distribution_method(preds)
        evaluation = evaluate_model(HUBER_MODEL_NAME, preds, method)
        print(f"  distribution method: {method}")
        print(f"  overall: n={evaluation.point.n:,} mae={evaluation.point.mae:.4f} rmse={evaluation.point.rmse:.4f} bias={evaluation.point.bias:+.4f}")

        ridge_run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == RIDGE_MODEL_NAME))
        if ridge_run is None:
            print(f"WARNING: no existing {RIDGE_MODEL_NAME!r} run found to preserve/un-promote.")
        else:
            print(f"Existing {RIDGE_MODEL_NAME!r} run (id={ridge_run.id}, is_promoted={ridge_run.is_promoted}) — will be un-promoted, not touched otherwise.")

        print(f"\nPersisting {HUBER_MODEL_NAME!r} as a new, promoted PlayerModelRun...")
        huber_run = persist_model_run(
            db, model_name=HUBER_MODEL_NAME, feature_names=PROMOTED_DISPOSAL_FEATURE_NAMES,
            config={"epsilon": 1.35, "alpha": 0.001}, distribution_method=method,
            tune_start_year=tune_start_year, tune_end_year=tune_end_year,
            evaluation=evaluation, predictions=preds, is_promoted=True,
        )
        print(f"  {HUBER_MODEL_NAME!r} persisted: id={huber_run.id} run_at={huber_run.run_at.isoformat()} is_promoted={huber_run.is_promoted}")

        if ridge_run is not None:
            ridge_run.is_promoted = False
            db.commit()
            db.refresh(ridge_run)
            print(f"  {RIDGE_MODEL_NAME!r} (id={ridge_run.id}) is_promoted now {ridge_run.is_promoted} — predictions/metrics untouched.")

        promoted = db.scalars(select(PlayerModelRun).where(PlayerModelRun.market == "player_disposals", PlayerModelRun.is_promoted.is_(True))).all()
        print(f"\nInvariant check: {len(promoted)} promoted disposal model run(s): {[r.model_name for r in promoted]}")
        if len(promoted) != 1 or promoted[0].model_name != HUBER_MODEL_NAME:
            print("  ERROR: invariant violated — more than one (or the wrong) promoted disposal model run exists.")
            return 1
        print("  OK — exactly one promoted disposal model run, and it's Huber.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
