"""Regenerates live disposal projections under the newly-promoted Huber
model, and reports a side-by-side Ridge-vs-Huber comparison on the current
upcoming round (Elite Disposal Model: Promotion stage).

Ridge's side of the comparison is fit fresh on the SAME full historical
data Huber's live fit uses (apples-to-apples - both "as of right now"),
using the OLD (now un-promoted) disposals_ridge PlayerModelRun's own
persisted config/feature_names, scored on the exact same feature vectors
(`input_features`, frozen per player on the live projection result) the
promoted Huber projection was generated from — no live/future outcome
data is used anywhere in this comparison, only already-completed history
plus the current round's pre-match features.

Does NOT touch old PricingSnapshot rows (no backfill, nothing here writes
to that table) and does NOT call any paid API (pure DB read + in-process
model fit).

Run: python -m scripts.regenerate_disposal_projections_and_compare
"""

import numpy as np
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Player, PlayerModelRun
from app.player_modelling.disposal_backtest import build_dataset
from app.player_modelling.disposal_features import PROMOTED_DISPOSAL_FEATURE_NAMES
from app.player_modelling.disposal_models import fit_ridge
from app.player_modelling.live_engine import generate_live_projections
from app.player_modelling.live_persistence import persist_projection_run


def main() -> int:
    db = SessionLocal()
    try:
        print("Regenerating live projections under the promoted model (Huber)...")
        run = generate_live_projections(db)
        if not run.disposal_projections:
            print("No upcoming disposal projections to generate (no upcoming round / no expected players).")
            return 0
        n_disposals, n_goals = persist_projection_run(db, run)
        print(f"  persisted: {n_disposals} disposal + {n_goals} goal projection(s)")
        print(f"  disposal_model_version now: {run.disposal_model_version}")

        print("\nFitting Ridge fresh on the same current history for a fair side-by-side...")
        ridge_run = db.scalar(select(PlayerModelRun).where(PlayerModelRun.model_name == "disposals_ridge"))
        all_rows = build_dataset(db).all_rows
        ridge_fitted = fit_ridge(all_rows, PROMOTED_DISPOSAL_FEATURE_NAMES, alpha=(ridge_run.config_json or {}).get("alpha", 5.0))

        X = np.array([
            [p.input_features.get(name) if p.input_features.get(name) is not None else np.nan for name in PROMOTED_DISPOSAL_FEATURE_NAMES]
            for p in run.disposal_projections
        ])
        ridge_pred = ridge_fitted.predict_fn(X)

        players = {p.id: p.display_name for p in db.scalars(select(Player)).all()}

        rows = []
        for proj, r_pred in zip(run.disposal_projections, ridge_pred):
            career_avg = proj.input_features.get("disposals_career_avg") or 0.0
            diff = proj.predicted_mean - float(r_pred)  # huber - ridge
            rows.append({
                "player": players.get(proj.player_id, f"player {proj.player_id}"),
                "match_id": proj.match_id, "games_of_history": proj.games_of_history,
                "career_avg": career_avg, "ridge": float(r_pred), "huber": proj.predicted_mean, "diff": diff,
            })

        diffs = np.array([r["diff"] for r in rows])
        print(f"\nn players compared: {len(rows)}")
        print(f"mean diff (huber - ridge): {diffs.mean():+.3f}   mean |diff|: {np.abs(diffs).mean():.3f}")
        for bound in (0.5, 1.0, 2.0):
            n_moved = int((np.abs(diffs) > bound).sum())
            print(f"  moved by >{bound}: {n_moved} ({n_moved/len(rows):.1%})")

        established_25 = [r for r in rows if r["career_avg"] >= 25 and r["games_of_history"] >= 20]
        low_history = [r for r in rows if r["games_of_history"] < 10]
        if established_25:
            d = np.array([r["diff"] for r in established_25])
            print(f"\nEstablished 25+ avg players (n={len(established_25)}): mean diff={d.mean():+.3f}  (huber - ridge; positive = huber now predicts MORE)")
        if low_history:
            d = np.array([r["diff"] for r in low_history])
            print(f"Low-history (<10 games) players (n={len(low_history)}): mean diff={d.mean():+.3f}")

        rows_sorted = sorted(rows, key=lambda r: r["diff"])
        print("\nLargest DECREASES (huber < ridge):")
        for r in rows_sorted[:8]:
            print(f"  {r['player']:<28} career_avg={r['career_avg']:.1f} games={r['games_of_history']:>3}  ridge={r['ridge']:.1f} huber={r['huber']:.1f} diff={r['diff']:+.2f}")
        print("Largest INCREASES (huber > ridge):")
        for r in rows_sorted[-8:][::-1]:
            print(f"  {r['player']:<28} career_avg={r['career_avg']:.1f} games={r['games_of_history']:>3}  ridge={r['ridge']:.1f} huber={r['huber']:.1f} diff={r['diff']:+.2f}")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
