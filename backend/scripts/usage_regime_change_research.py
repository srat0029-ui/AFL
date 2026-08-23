"""Research script (Usage-Change / Regime-Change Research stage): does a
leakage-safe "how different is this player's recent usage from their own
longer-run baseline" signal identify rows where the CURRENT PROMOTED models
are less reliable, and — if so — can that be used to widen uncertainty
(interval/alpha) rather than changing the point prediction?

READ-ONLY RESEARCH — never touches a promoted model row, never writes to the
DB, never changes a point prediction. Reuses the promoted Huber/hurdle point
predictions exactly as produced today; the only new artifact tested is an
ALTERNATIVE DISPERSION (nb_alpha) applied post-hoc to the SAME predicted
means, so any effect measured here is purely an uncertainty/calibration
effect, never a point-accuracy effect.

Reuses app/player_modelling/usage_regime.py's leakage-safe role/usage
profile builder and fit_usage_regime_model/usage_regime_for directly (that
module is the promoted production home for this exact method, moved there
in the Usage-Change Production Integration stage — this script no longer
keeps its own copy) — this script adds no archetype clustering (explicitly
out of scope that stage) and computes only a single change_score per row:
the standardized Euclidean distance between a player's last-5-game usage
profile and their own prior (games -40..-6) profile, using a scaler fit on
TUNE-period rows only.

The stable/changed cutoff is fixed from the TUNE distribution (median of
tune-period change_score) and applied unchanged to eval rows — a real,
deployable threshold decided before looking at eval data, not one re-derived
from the eval set's own distribution.

Run: python -m scripts.usage_regime_change_research
"""

from collections import defaultdict
from dataclasses import replace

import numpy as np

from app.database import SessionLocal
from app.player_modelling.disposal_backtest import EVALUATION_START_YEAR, build_dataset, run_candidate_models
from app.player_modelling.disposal_data import load_team_game_rows
from app.player_modelling.disposal_evaluation import CALIBRATION_THRESHOLDS, compute_interval_metrics, compute_threshold_metrics, evaluate_model
from app.player_modelling.disposal_features import PROMOTED_DISPOSAL_FEATURE_NAMES
from app.player_modelling.disposal_models import fit_residual_nb_alpha, target_vector

from app.player_modelling.goal_backtest import build_goal_dataset, run_goal_candidate_models
from app.player_modelling.goal_evaluation import compute_threshold_metrics as goal_threshold_metrics, compute_zero_goal_calibration, evaluate_goal_model
from app.player_modelling.goal_features import PLAYER_FEATURE_NAMES as GOAL_PLAYER_FEATURE_NAMES

from app.player_modelling.usage_regime import (
    ROLE_CHANGE_MIN_GAMES,
    ROLE_DIMS,
    RoleRow,
    build_role_rows,
    fit_usage_regime_model,
    load_raw_player_rows,
    usage_regime_for,
)


def compute_change_scores(role_rows: list[RoleRow]) -> tuple[dict[tuple[int, int], float], float]:
    """Thin wrapper around the production fit_usage_regime_model/
    usage_regime_for (see their docstrings for the exact method) - returns
    {(player_id, match_id): change_score} for every row with enough history,
    plus the tune-derived median cutoff this research script's downstream
    stable/changed bucketing (STEP 1 onward, below) already expects."""
    model = fit_usage_regime_model(role_rows)
    scores: dict[tuple[int, int], float] = {}
    for r in role_rows:
        result = usage_regime_for(r, model)
        if result.usage_change_score is not None:
            scores[(r.player_id, r.match_id)] = result.usage_change_score
    return scores, model.cutoff


def main() -> None:
    db = SessionLocal()
    try:
        split = build_dataset(db)
        gsplit = build_goal_dataset(db)
        raw_rows = load_raw_player_rows(db)
        team_rows = load_team_game_rows(db)
    finally:
        db.close()

    team_disposals_by_match = {(t.team_id, t.match_id): t.disposals for t in team_rows if t.disposals is not None}
    role_rows = build_role_rows(raw_rows, team_disposals_by_match)
    change_scores, cutoff = compute_change_scores(role_rows)
    print(f"tune rows: {len(split.tune_rows):,}  eval rows: {len(split.eval_rows):,}")
    print(f"change_score computed for {len(change_scores):,}/{len(role_rows):,} rows  (tune-derived stable/changed cutoff = {cutoff:.3f})\n")

    # ============================================================
    # 1) Persistence: is a "changed" reading sticky (real regime shift) or
    #    does it revert immediately (one noisy game)?
    # ============================================================
    print("=" * 70)
    print("STEP 1: Persistence of the changed flag")
    print("=" * 70)
    by_player: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for r in role_rows:
        by_player[r.player_id].append((r.player_id, r.match_id))

    flagged_total, changed_total, changed_to_changed = 0, 0, 0
    for player_id, keys in by_player.items():
        flags = [change_scores[k] >= cutoff for k in keys if k in change_scores]
        for i in range(len(flags) - 1):
            flagged_total += 1
            if flags[i]:
                changed_total += 1
                if flags[i + 1]:
                    changed_to_changed += 1
    base_rate = changed_total / flagged_total if flagged_total else float("nan")
    persistence = changed_to_changed / changed_total if changed_total else float("nan")
    print(f"base rate P(changed) = {base_rate:.3f}")
    print(f"P(changed at t+1 | changed at t) = {persistence:.3f}  (vs base rate {base_rate:.3f} if change_score carried no persistence)")
    print("-> " + ("Persistent signal: a changed reading raises the odds of the NEXT game also reading changed." if persistence > base_rate + 0.03 else "Weak/no persistence: a changed reading looks close to a one-off, not a sustained regime shift."))

    # ============================================================
    # 2) Merge change_score onto disposal/goal eval rows; get PROMOTED
    #    predictions exactly as production computes them (point estimate
    #    untouched throughout this script).
    # ============================================================
    for r in split.all_rows:
        cs = change_scores.get((r.player_id, r.match_id))
        if cs is not None:
            r.features["_change_score"] = cs
    for r in gsplit.all_rows:
        cs = change_scores.get((r.player_id, r.match_id))
        if cs is not None:
            r.features["_change_score"] = cs

    disposal_preds = run_candidate_models(split, feature_names=PROMOTED_DISPOSAL_FEATURE_NAMES, model_names=("huber",))["huber"]
    goal_preds = run_goal_candidate_models(gsplit, feature_names=GOAL_PLAYER_FEATURE_NAMES, model_names=("hurdle",))["hurdle"]

    disposal_rows_by_key = {(r.player_id, r.match_id): r for r in split.eval_rows}
    goal_rows_by_key = {(r.player_id, r.match_id): r for r in gsplit.eval_rows}

    def bucket_disposal(preds):
        stable, changed = [], []
        for p in preds:
            cs = disposal_rows_by_key[(p.player_id, p.match_id)].features.get("_change_score")
            if cs is None:
                continue
            (changed if cs >= cutoff else stable).append(p)
        return stable, changed

    def bucket_goal(preds):
        stable, changed = [], []
        for p in preds:
            cs = goal_rows_by_key[(p.player_id, p.match_id)].features.get("_change_score")
            if cs is None:
                continue
            (changed if cs >= cutoff else stable).append(p)
        return stable, changed

    disposal_stable, disposal_changed = bucket_disposal(disposal_preds)
    goal_stable, goal_changed = bucket_goal(goal_preds)

    # ============================================================
    # 3) Error difference: disposal MAE/bias, threshold Brier/ECE
    # ============================================================
    print("\n" + "=" * 70)
    print("STEP 2: Disposal — stable vs changed (promoted Huber, point estimate untouched)")
    print("=" * 70)
    ev_stable, ev_changed = evaluate_model("stable", disposal_stable), evaluate_model("changed", disposal_changed)
    print(f"{'':>10} {'n':>7} {'MAE':>7} {'RMSE':>7} {'bias':>8}", end="")
    for t in CALIBRATION_THRESHOLDS:
        print(f"  Brier@{t:<3}  ECE@{t:<3}", end="")
    print()
    for label, ev in (("stable", ev_stable), ("changed", ev_changed)):
        line = f"{label:>10} {ev.point.n:>7} {ev.point.mae:>7.3f} {ev.point.rmse:>7.3f} {ev.point.bias:>+8.3f}"
        for t in CALIBRATION_THRESHOLDS:
            ece = f"{ev.thresholds[t].ece:.4f}" if ev.thresholds[t].ece is not None else "n/a"
            line += f"  {ev.thresholds[t].brier:>9.4f}  {ece:>7}"
        print(line)
    print(f"\nMAE delta (changed - stable): {ev_changed.point.mae - ev_stable.point.mae:+.3f}   ({(ev_changed.point.mae / ev_stable.point.mae - 1):+.1%} relative)")

    # ============================================================
    # 4) Interval coverage: baseline single global alpha
    # ============================================================
    print("\n" + "=" * 70)
    print("STEP 3: Interval coverage — baseline (single global alpha, current production behaviour)")
    print("=" * 70)
    print(f"{'coverage':>10} {'stable_emp':>11} {'changed_emp':>12}  (target)")
    for c in (0.5, 0.8, 0.9):
        s = compute_interval_metrics(disposal_stable, c).empirical_coverage
        ch = compute_interval_metrics(disposal_changed, c).empirical_coverage
        print(f"{c:>10.0%} {s:>11.3f} {ch:>12.3f}   ({c:.0%})")

    # ============================================================
    # 5) Change-conditional alpha (fit on TUNE only): does widening
    #    dispersion for "changed" rows fix under-coverage, if any exists?
    # ============================================================
    print("\n" + "=" * 70)
    print("STEP 4: Change-conditional dispersion (fit on tune only) — point estimate STILL untouched")
    print("=" * 70)
    # Refit the SAME promoted Huber config on tune rows only, to get tune-period predicted means for fitting tune-conditional alphas
    from app.player_modelling.disposal_models import feature_matrix, fit_huber
    fitted = fit_huber(split.tune_rows, PROMOTED_DISPOSAL_FEATURE_NAMES)
    X_tune = feature_matrix(split.tune_rows, PROMOTED_DISPOSAL_FEATURE_NAMES)
    tune_pred = fitted.predict_fn(X_tune)
    tune_actual = target_vector(split.tune_rows)
    tune_cs = [r.features.get("_change_score") for r in split.tune_rows]
    tune_stable_mask = np.array([cs is not None and cs < cutoff for cs in tune_cs])
    tune_changed_mask = np.array([cs is not None and cs >= cutoff for cs in tune_cs])
    alpha_stable = fit_residual_nb_alpha(tune_pred[tune_stable_mask], tune_actual[tune_stable_mask])
    alpha_changed = fit_residual_nb_alpha(tune_pred[tune_changed_mask], tune_actual[tune_changed_mask])
    print(f"tune-fit dispersion: alpha_stable={alpha_stable:.4f}  alpha_changed={alpha_changed:.4f}  (single global alpha currently used in production: {disposal_stable[0].nb_alpha:.4f})")

    adjusted_changed = [replace(p, nb_alpha=alpha_changed) for p in disposal_changed]
    adjusted_stable = [replace(p, nb_alpha=alpha_stable) for p in disposal_stable]
    print(f"\n{'coverage':>10} {'changed_baseline':>17} {'changed_adjusted':>17}  (target)")
    for c in (0.5, 0.8, 0.9):
        base_cov = compute_interval_metrics(disposal_changed, c).empirical_coverage
        adj_cov = compute_interval_metrics(adjusted_changed, c).empirical_coverage
        print(f"{c:>10.0%} {base_cov:>17.3f} {adj_cov:>17.3f}   ({c:.0%})")

    print(f"\n{'threshold':>10} {'ECE_changed_baseline':>21} {'ECE_changed_adjusted':>21}")
    for t in CALIBRATION_THRESHOLDS:
        base_ece = compute_threshold_metrics(disposal_changed, t).ece
        adj_ece = compute_threshold_metrics(adjusted_changed, t).ece
        base_s = f"{base_ece:.4f}" if base_ece is not None else "n/a"
        adj_s = f"{adj_ece:.4f}" if adj_ece is not None else "n/a"
        print(f"{t:>10} {base_s:>21} {adj_s:>21}")

    print("\nStable bucket check (must not get worse from a stable-conditional alpha):")
    for c in (0.5, 0.8, 0.9):
        base_cov = compute_interval_metrics(disposal_stable, c).empirical_coverage
        adj_cov = compute_interval_metrics(adjusted_stable, c).empirical_coverage
        print(f"  {c:>10.0%} baseline={base_cov:.3f} adjusted={adj_cov:.3f}")

    # ============================================================
    # 6) Goals: calibration difference, stable vs changed
    # ============================================================
    print("\n" + "=" * 70)
    print("STEP 5: Goals — stable vs changed (promoted hurdle, point estimate untouched)")
    print("=" * 70)
    gev_stable, gev_changed = evaluate_goal_model("stable", goal_stable), evaluate_goal_model("changed", goal_changed)
    print(f"{'':>10} {'n':>7} {'MAE':>7} {'bias':>8}", end="")
    for t in (1, 2, 3):
        print(f"  ECE@{t:<3}", end="")
    print("  P0_ECE")
    for label, ev, preds in (("stable", gev_stable, goal_stable), ("changed", gev_changed, goal_changed)):
        line = f"{label:>10} {ev.point.n:>7} {ev.point.mae:>7.3f} {ev.point.bias:>+8.3f}"
        for t in (1, 2, 3):
            ece = ev.thresholds[t].ece
            line += f"  {ece:.4f}" if ece is not None else "     n/a"
        p0 = compute_zero_goal_calibration(preds).ece
        line += f"   {p0:.4f}" if p0 is not None else "    n/a"
        print(line)

    print("\nDone. Research only — no model promoted, no point predictions changed, no DB writes.")


if __name__ == "__main__":
    main()
