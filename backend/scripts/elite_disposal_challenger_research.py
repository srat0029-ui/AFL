"""Research script (Elite Disposal Model Research + Challenger Evaluation):
reproduces the elite/high-volume disposal bias with a full breakdown,
investigates Ridge shrinkage via an alpha grid, and evaluates a small set
of defensible challengers (weaker Ridge, Huber, ElasticNet, player-baseline
+ contextual-residual) against the current promoted config.

READ-ONLY RESEARCH: never touches the promoted model row, never writes to
the database. Reuses the EXACT existing pipeline (disposal_data.py's
point-in-time-ordered load, disposal_features.py's leakage-safe feature
builder, disposal_backtest.py's chronological tune/eval split and
PredictionRecord wrapping, disposal_evaluation.py's metrics) — nothing
about data loading, feature construction, or the tune/eval boundary is
reimplemented here. Only the CANDIDATE MODEL FITTING functions below are
new, and only for this research pass.

Run: python -m scripts.elite_disposal_challenger_research
"""

from dataclasses import dataclass

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, HuberRegressor, Ridge
from sklearn.preprocessing import StandardScaler

from app.database import SessionLocal
from app.player_modelling.disposal_backtest import (
    EVALUATION_START_YEAR,
    PredictionRecord,
    _predict_and_wrap,
    build_dataset,
)
from app.player_modelling.disposal_evaluation import CALIBRATION_THRESHOLDS, evaluate_model, point_metrics_by_season
from app.player_modelling.disposal_features import PROMOTED_DISPOSAL_FEATURE_NAMES, DisposalFeatureRow
from app.player_modelling.disposal_models import feature_matrix, fit_residual_nb_alpha, target_vector

RANDOM_STATE = 42

# Contextual-only feature set for the baseline+residual challenger's
# residual model - deliberately EXCLUDES the player-level average features
# (last3/5/10/season/career/ewma/std), since those are already folded into
# the leakage-safe baseline below; the residual model's job is only to
# learn CONTEXTUAL deviation around that baseline.
CONTEXT_FEATURE_NAMES = (
    "tog_last3_avg", "tog_trend", "disposal_trend",
    "opponent_disposals_conceded_avg", "opponent_expected_score",
    "team_elo_win_prob", "team_expected_score", "expected_margin",
    "venue_disposals_env", "is_home",
)


# --- new candidate fitters (research-only; not added to disposal_models.py) ---

def fit_ridge_alpha(train_rows, feature_names, alpha: float):
    X, y = feature_matrix(train_rows, feature_names), target_vector(train_rows)
    imputer = SimpleImputer(strategy="median").fit(X)
    scaler = StandardScaler().fit(imputer.transform(X))
    model = Ridge(alpha=alpha, random_state=RANDOM_STATE).fit(scaler.transform(imputer.transform(X)), y)
    return lambda Xn: np.clip(model.predict(scaler.transform(imputer.transform(Xn))), 0, None), model


def fit_huber(train_rows, feature_names):
    X, y = feature_matrix(train_rows, feature_names), target_vector(train_rows)
    imputer = SimpleImputer(strategy="median").fit(X)
    scaler = StandardScaler().fit(imputer.transform(X))
    model = HuberRegressor(epsilon=1.35, alpha=0.001, max_iter=500).fit(scaler.transform(imputer.transform(X)), y)
    return lambda Xn: np.clip(model.predict(scaler.transform(imputer.transform(Xn))), 0, None)


def fit_elasticnet(train_rows, feature_names, alpha: float = 0.1, l1_ratio: float = 0.3):
    X, y = feature_matrix(train_rows, feature_names), target_vector(train_rows)
    imputer = SimpleImputer(strategy="median").fit(X)
    scaler = StandardScaler().fit(imputer.transform(X))
    model = ElasticNet(alpha=alpha, l1_ratio=l1_ratio, random_state=RANDOM_STATE, max_iter=5000).fit(scaler.transform(imputer.transform(X)), y)
    return lambda Xn: np.clip(model.predict(scaler.transform(imputer.transform(Xn))), 0, None)


LEAGUE_AVG = 16.0
BASELINE_SHRINKAGE_K = 8.0  # games of history at which the blended player-level estimate gets half-weight vs league avg


def _player_baseline(row: DisposalFeatureRow) -> float:
    """Leakage-safe (built purely from this row's own already-point-in-time
    features): a shrinkage-weighted blend of season/EWMA/career averages,
    shrunk toward the league average for low-history players. Every input
    is computed strictly before this game (see disposal_features.py)."""
    f = row.features
    parts = [v for v in (f.get("disposals_season_avg"), f.get("disposals_ewma"), f.get("disposals_career_avg")) if v is not None]
    player_level = sum(parts) / len(parts) if parts else LEAGUE_AVG
    n = row.games_of_history
    weight = n / (n + BASELINE_SHRINKAGE_K)
    return weight * player_level + (1 - weight) * LEAGUE_AVG


def fit_baseline_residual(train_rows, context_feature_names=CONTEXT_FEATURE_NAMES, alpha: float = 1.0):
    baselines_train = np.array([_player_baseline(r) for r in train_rows])
    y = target_vector(train_rows)
    residual_target = y - baselines_train

    X = feature_matrix(train_rows, context_feature_names)
    imputer = SimpleImputer(strategy="median").fit(X)
    scaler = StandardScaler().fit(imputer.transform(X))
    model = Ridge(alpha=alpha, random_state=RANDOM_STATE).fit(scaler.transform(imputer.transform(X)), residual_target)

    def predict_fn(rows):
        baselines = np.array([_player_baseline(r) for r in rows])
        Xn = feature_matrix(rows, context_feature_names)
        adj = model.predict(scaler.transform(imputer.transform(Xn)))
        return np.clip(baselines + adj, 0, None)

    return predict_fn


# --- bucketing helpers for the multi-dimensional bias breakdown ---

def _bucket(value, edges, labels):
    if value is None:
        return None
    for lo, hi, label in zip(edges[:-1], edges[1:], labels):
        if lo <= value < hi:
            return label
    return labels[-1]


VOLUME_EDGES = [0, 15, 22, 25, 28, 100]
VOLUME_LABELS = ["<15", "15-22", "22-25", "25-28", "28+"]
GAMES_EDGES = [0, 5, 10, 20, 10_000]
GAMES_LABELS = ["<5", "5-9", "10-19", "20+"]
TOG_EDGES = [0, 60, 75, 85, 101]
TOG_LABELS = ["<60%", "60-75%", "75-85%", "85%+"]


def bias_breakdown(predictions: list[PredictionRecord], rows_by_key: dict, dimension_fn, label: str) -> None:
    groups: dict[str, list[float]] = {}
    for p in predictions:
        key = dimension_fn(p, rows_by_key[(p.player_id, p.match_id)])
        groups.setdefault(key, []).append(p.predicted_mean - p.actual)
    print(f"  {label}:")
    for k in sorted(g for g in groups if g is not None):
        vals = groups[k]
        print(f"    {k:>10}: n={len(vals):>6}  bias={sum(vals)/len(vals):+.3f}")


def main() -> None:
    db = SessionLocal()
    try:
        split = build_dataset(db)
    finally:
        db.close()

    print(f"tune rows: {len(split.tune_rows):,} (pre-{EVALUATION_START_YEAR})  eval rows: {len(split.eval_rows):,} ({EVALUATION_START_YEAR}+)\n")
    rows_by_key = {(r.player_id, r.match_id): r for r in split.eval_rows}

    # ============================================================
    # 1) Reproduce current promoted model's bias, broken down every requested way
    # ============================================================
    print("=" * 70)
    print("STEP 1: Reproduce current promoted Ridge(alpha=5.0) bias breakdown")
    print("=" * 70)
    predict_current, ridge_model = fit_ridge_alpha(split.tune_rows, PROMOTED_DISPOSAL_FEATURE_NAMES, alpha=5.0)
    X_eval = feature_matrix(split.eval_rows, PROMOTED_DISPOSAL_FEATURE_NAMES)
    X_tune = feature_matrix(split.tune_rows, PROMOTED_DISPOSAL_FEATURE_NAMES)
    tune_pred = predict_current(X_tune)
    alpha_nb = fit_residual_nb_alpha(tune_pred, target_vector(split.tune_rows))
    residuals = np.sort(target_vector(split.tune_rows) - tune_pred)
    current_preds = _predict_and_wrap("ridge_current", predict_current(X_eval), split.eval_rows, alpha_nb, residuals)

    print(f"Overall: n={len(current_preds):,} MAE={evaluate_model('cur', current_preds).point.mae:.3f} bias={evaluate_model('cur', current_preds).point.bias:+.3f}\n")

    bias_breakdown(current_preds, rows_by_key, lambda p, r: _bucket(r.features.get("disposals_career_avg"), VOLUME_EDGES, VOLUME_LABELS), "by historical (career) average")
    bias_breakdown(current_preds, rows_by_key, lambda p, r: _bucket(r.features.get("disposals_last5_avg"), VOLUME_EDGES, VOLUME_LABELS), "by recent 5-game average")
    bias_breakdown(current_preds, rows_by_key, lambda p, r: _bucket(r.features.get("disposals_last10_avg"), VOLUME_EDGES, VOLUME_LABELS), "by recent 10-game average")
    bias_breakdown(current_preds, rows_by_key, lambda p, r: _bucket(r.features.get("disposals_season_avg"), VOLUME_EDGES, VOLUME_LABELS), "by season average")
    bias_breakdown(current_preds, rows_by_key, lambda p, r: _bucket(p.games_of_history, GAMES_EDGES, GAMES_LABELS), "by sample size (games_of_history)")
    bias_breakdown(current_preds, rows_by_key, lambda p, r: _bucket(r.features.get("tog_last5_avg"), TOG_EDGES, TOG_LABELS), "by TOG (last5 avg)")
    bias_breakdown(current_preds, rows_by_key, lambda p, r: str(p.season_year), "by season")
    print("  (player experience == games_of_history above; no separate 'career games' field exists distinct from it)\n")

    # ============================================================
    # 2) Ridge alpha grid
    # ============================================================
    print("=" * 70)
    print("STEP 2: Ridge alpha grid (chronological tune/eval, PROMOTED feature set)")
    print("=" * 70)
    print(f"{'alpha':>8} {'MAE':>7} {'RMSE':>7} {'bias':>8} {'elite28+ bias':>14} {'ECE@25':>8} {'ECE@30':>8}")
    for alpha in (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0):
        pred_fn, _ = fit_ridge_alpha(split.tune_rows, PROMOTED_DISPOSAL_FEATURE_NAMES, alpha)
        tp = pred_fn(X_tune)
        a = fit_residual_nb_alpha(tp, target_vector(split.tune_rows))
        res = np.sort(target_vector(split.tune_rows) - tp)
        preds = _predict_and_wrap(f"ridge_{alpha}", pred_fn(X_eval), split.eval_rows, a, res)
        ev = evaluate_model(f"ridge_{alpha}", preds)
        elite_vals = [p.predicted_mean - p.actual for p in preds if (rows_by_key[(p.player_id, p.match_id)].features.get("disposals_career_avg") or 0) >= 28]
        elite_bias = sum(elite_vals) / len(elite_vals) if elite_vals else float("nan")
        ece25 = ev.thresholds[25].ece
        ece30 = ev.thresholds[30].ece
        marker = " <- current" if alpha == 5.0 else ""
        print(f"{alpha:>8.1f} {ev.point.mae:>7.3f} {ev.point.rmse:>7.3f} {ev.point.bias:>+8.3f} {elite_bias:>+14.3f} {ece25:>8.4f} {ece30:>8.4f}{marker}")

    # ============================================================
    # 3-5) Challengers
    # ============================================================
    print("\n" + "=" * 70)
    print("STEP 3-5: Challenger models")
    print("=" * 70)

    challengers: dict[str, list[PredictionRecord]] = {"current_ridge_a5": current_preds}

    # weaker ridge (best-looking alpha from the grid, chosen on OVERALL MAE/calibration, not elite bias alone)
    weak_alpha = 1.0
    pred_fn, _ = fit_ridge_alpha(split.tune_rows, PROMOTED_DISPOSAL_FEATURE_NAMES, weak_alpha)
    tp = pred_fn(X_tune)
    a = fit_residual_nb_alpha(tp, target_vector(split.tune_rows))
    res = np.sort(target_vector(split.tune_rows) - tp)
    challengers[f"ridge_alpha_{weak_alpha}"] = _predict_and_wrap("ridge_weak", pred_fn(X_eval), split.eval_rows, a, res)

    # Huber
    pred_fn = fit_huber(split.tune_rows, PROMOTED_DISPOSAL_FEATURE_NAMES)
    tp = pred_fn(X_tune)
    a = fit_residual_nb_alpha(tp, target_vector(split.tune_rows))
    res = np.sort(target_vector(split.tune_rows) - tp)
    challengers["huber"] = _predict_and_wrap("huber", pred_fn(X_eval), split.eval_rows, a, res)

    # ElasticNet
    pred_fn = fit_elasticnet(split.tune_rows, PROMOTED_DISPOSAL_FEATURE_NAMES)
    tp = pred_fn(X_tune)
    a = fit_residual_nb_alpha(tp, target_vector(split.tune_rows))
    res = np.sort(target_vector(split.tune_rows) - tp)
    challengers["elasticnet"] = _predict_and_wrap("elasticnet", pred_fn(X_eval), split.eval_rows, a, res)

    # baseline + contextual residual
    pred_fn = fit_baseline_residual(split.tune_rows)
    tp = pred_fn(split.tune_rows)
    a = fit_residual_nb_alpha(tp, target_vector(split.tune_rows))
    res = np.sort(target_vector(split.tune_rows) - tp)
    challengers["baseline_plus_residual"] = _predict_and_wrap("baseline_residual", pred_fn(split.eval_rows), split.eval_rows, a, res)

    # ============================================================
    # Evaluate every challenger: overall, high-volume, low-history, calibration, season
    # ============================================================
    print(f"\n{'model':>22} {'MAE':>7} {'RMSE':>7} {'bias':>8} {'22+bias':>9} {'25+bias':>9} {'28+bias':>9}")
    for name, preds in challengers.items():
        ev = evaluate_model(name, preds)
        rows = [(p, rows_by_key[(p.player_id, p.match_id)]) for p in preds]
        b22 = np.mean([p.predicted_mean - p.actual for p, r in rows if (r.features.get("disposals_career_avg") or 0) >= 22]) if rows else float("nan")
        b25 = np.mean([p.predicted_mean - p.actual for p, r in rows if (r.features.get("disposals_career_avg") or 0) >= 25]) if rows else float("nan")
        b28 = np.mean([p.predicted_mean - p.actual for p, r in rows if (r.features.get("disposals_career_avg") or 0) >= 28]) if rows else float("nan")
        print(f"{name:>22} {ev.point.mae:>7.3f} {ev.point.rmse:>7.3f} {ev.point.bias:>+8.3f} {b22:>+9.3f} {b25:>+9.3f} {b28:>+9.3f}")

    print(f"\n{'model':>22} {'n<5':>6} {'bias<5':>8} {'n5-9':>6} {'bias5-9':>8} {'n10-19':>7} {'bias10-19':>10} {'n20+':>7} {'bias20+':>8}")
    for name, preds in challengers.items():
        line = f"{name:>22}"
        for lo, hi in ((0, 5), (5, 10), (10, 20), (20, 10_000)):
            vals = [p.predicted_mean - p.actual for p in preds if lo <= p.games_of_history < hi]
            line += f" {len(vals):>6} {(sum(vals)/len(vals) if vals else float('nan')):>+8.3f}" if hi < 10_000 else f" {len(vals):>7} {(sum(vals)/len(vals) if vals else float('nan')):>+8.3f}"
        print(line)

    print(f"\n{'model':>22}", end="")
    for t in CALIBRATION_THRESHOLDS:
        print(f"  ECE@{t:<3}", end="")
    print("  interval80cov")
    for name, preds in challengers.items():
        ev = evaluate_model(name, preds)
        line = f"{name:>22}"
        for t in CALIBRATION_THRESHOLDS:
            line += f"  {ev.thresholds[t].ece:.4f}" if ev.thresholds[t].ece is not None else "     n/a"
        line += f"      {ev.intervals[0.8].empirical_coverage:.3f}"
        print(line)

    print("\nSeason-by-season MAE (consistency check):")
    for name, preds in challengers.items():
        by_season = point_metrics_by_season(preds)
        line = f"{name:>22}: " + " ".join(f"{yr}={m.mae:.2f}" for yr, m in sorted(by_season.items()))
        print(line)

    print("\nDone. Research only — no model promoted or written to the DB.")


if __name__ == "__main__":
    main()
