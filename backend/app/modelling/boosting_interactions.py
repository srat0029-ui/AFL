"""Interaction analysis for gradient boosting — inspects whether the model
learned meaningful (non-additive) relationships between pairs of features,
using XGBoost's native SHAP interaction values. Every reported number is a
direct readout of the fitted model's own behaviour on real evaluation
matches; nothing here asserts a football explanation is true, only how
strongly the model's predictions actually move when two features vary
together versus independently.

Only supported for XGBoost — LightGBM has no equivalent native interaction
API without the separate `shap` package, which this project avoids adding
(see boosting.py's module docstring on why XGBoost's native contributions
were preferred over the `shap` dependency in the first place). If the best
candidate turns out to be LightGBM, interaction findings are reported as
unavailable rather than approximated.
"""

from dataclasses import dataclass

import numpy as np

from app.modelling.features import MatchFeatureRow
from app.modelling.logistic import feature_matrix

# The specific hypotheses named in the Stage brief, each mapped to a real
# pair of columns already in the model's feature set (where available).
HYPOTHESIS_PAIRS: list[tuple[str, str, str]] = [
    ("elo_home_win_probability", "inside50_differential_diff", "Inside-50 advantage vs. Elo closeness"),
    ("elo_home_win_probability", "points_for_diff_5", "Recent scoring form vs. Elo advantage"),
    ("elo_home_win_probability", "clearance_differential_diff", "Clearance advantage vs. Elo closeness"),
    ("elo_home_win_probability", "form_diff_5", "Recent form vs. favourite/underdog status"),
    ("elo_home_win_probability", "form_diff_10", "Longer-run form vs. favourite/underdog status"),
]


@dataclass(frozen=True)
class InteractionFinding:
    feature_a: str
    feature_b: str
    label: str
    mean_abs_interaction: float
    # main-effect magnitudes for context: an interaction only "matters" if
    # it's a non-trivial fraction of how much either feature moves
    # predictions on its own.
    mean_abs_main_effect_a: float
    mean_abs_main_effect_b: float


def supports_interactions(model) -> bool:
    return hasattr(model, "get_booster")


def shap_values(model, X: np.ndarray) -> np.ndarray | None:
    """(n_samples, n_features) array of per-feature SHAP contributions, or
    None if unsupported."""
    if not supports_interactions(model):
        return None
    import xgboost as xgb

    contribs = model.get_booster().predict(xgb.DMatrix(X), pred_contribs=True)
    return contribs[:, :-1]  # drop the trailing bias column


def shap_interactions(model, X: np.ndarray) -> np.ndarray | None:
    """(n_samples, n_features, n_features) array of pairwise SHAP
    interaction values, or None if unsupported."""
    if not supports_interactions(model):
        return None
    import xgboost as xgb

    interactions = model.get_booster().predict(xgb.DMatrix(X), pred_interactions=True)
    return interactions[:, :-1, :-1]  # drop the bias row/column


def analyze_interactions(
    model, rows: list[MatchFeatureRow], feature_names: tuple[str, ...], top_n: int = 6
) -> list[InteractionFinding]:
    """Top `top_n` feature pairs by mean |SHAP interaction| across `rows`,
    plus the hypothesis pairs named in the Stage brief (even if they don't
    make the top N) — reported with real computed magnitudes, never
    invented text."""
    if not rows:
        return []
    X = feature_matrix(rows, feature_names)
    interactions = shap_interactions(model, X)
    if interactions is None:
        return []
    main_effects = shap_values(model, X)

    n = len(feature_names)
    main_effect_abs = {feature_names[i]: float(np.mean(np.abs(main_effects[:, i]))) for i in range(n)}

    pair_strength: dict[tuple[str, str], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            pair_strength[(feature_names[i], feature_names[j])] = float(np.mean(np.abs(interactions[:, i, j])))

    top_pairs = sorted(pair_strength.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    seen = {pair for pair, _ in top_pairs}

    findings = []
    for (a, b), strength in top_pairs:
        findings.append(
            InteractionFinding(
                feature_a=a, feature_b=b, label=f"{a} x {b}",
                mean_abs_interaction=strength,
                mean_abs_main_effect_a=main_effect_abs.get(a, 0.0),
                mean_abs_main_effect_b=main_effect_abs.get(b, 0.0),
            )
        )

    for feat_a, feat_b, label in HYPOTHESIS_PAIRS:
        if feat_a not in feature_names or feat_b not in feature_names:
            continue
        pair = (feat_a, feat_b) if (feat_a, feat_b) in pair_strength else (feat_b, feat_a)
        if pair not in pair_strength or pair in seen:
            continue  # not a valid column pair, or already reported among the top-N
        findings.append(
            InteractionFinding(
                feature_a=feat_a, feature_b=feat_b, label=label,
                mean_abs_interaction=pair_strength[pair],
                mean_abs_main_effect_a=main_effect_abs.get(feat_a, 0.0),
                mean_abs_main_effect_b=main_effect_abs.get(feat_b, 0.0),
            )
        )

    return findings
