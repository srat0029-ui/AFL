"""Feature usefulness diagnostics: standardized coefficients, permutation
importance, and single/grouped feature-set ablation against held-out data.

Raw logistic-regression coefficient magnitude alone is misleading once
features are on different scales — see the Stage brief's explicit warning.
Because the fitted Pipeline always includes a StandardScaler before the
LogisticRegression step (see logistic.py), the coefficients extracted here
already act on standardized (mean-0, unit-variance) inputs, so they're
comparable to each other out of the box — no separate "standardization"
step needed beyond reading them off the fitted pipeline.

Permutation importance and ablation both answer "does this feature/group
earn its place" empirically, on held-out data, rather than by how
plausible it sounds — the actual point of this module. A feature with a
large coefficient but near-zero permutation importance is a sign of
correlation/redundancy with other features, not real independent signal.
"""

from dataclasses import dataclass

import numpy as np

from app.modelling.features import ELO_FEATURE_NAME, FEATURE_GROUPS, STATS_PLUS_ELO_FEATURE_NAMES, MatchFeatureRow
from app.modelling.logistic import LogisticConfig, feature_matrix, fit_logistic_model, predict
from app.modelling.metrics import brier_score
from app.modelling.metrics import log_loss as log_loss_fn

PERMUTATION_SEED = 42
PERMUTATION_REPEATS = 20


def standardized_coefficients(pipeline, feature_names: tuple[str, ...]) -> dict[str, float]:
    """Coefficients from the fitted LogisticRegression step, which — because
    StandardScaler runs immediately before it in the pipeline — already
    represent the change in log-odds per one-standard-deviation change in
    that feature, making them directly comparable across features."""
    coefs = pipeline.named_steps["logreg"].coef_[0]
    return dict(zip(feature_names, (float(c) for c in coefs)))


def permutation_importance(
    pipeline,
    rows: list[MatchFeatureRow],
    feature_names: tuple[str, ...],
    n_repeats: int = PERMUTATION_REPEATS,
    seed: int = PERMUTATION_SEED,
) -> dict[str, float]:
    """For each feature, shuffles that column across the evaluation rows
    (breaking its relationship with the outcome while preserving its
    marginal distribution and every other feature's values), re-scores
    Brier, and reports the average degradation vs. the unshuffled baseline
    over `n_repeats` shuffles. Positive = the feature helps (shuffling it
    hurts Brier); near-zero or negative = it isn't pulling weight on this
    evaluation set.
    """
    if not rows:
        return {name: float("nan") for name in feature_names}

    X = feature_matrix(rows, feature_names)
    class_index = list(pipeline.classes_).index(1.0)
    outcomes = [r.actual_home_outcome for r in rows]

    baseline_probs = pipeline.predict_proba(X)[:, class_index]
    baseline_brier = brier_score(list(baseline_probs), outcomes)

    rng = np.random.RandomState(seed)
    importances: dict[str, float] = {}
    for i, name in enumerate(feature_names):
        degradations = []
        for _ in range(n_repeats):
            X_shuffled = X.copy()
            rng.shuffle(X_shuffled[:, i])
            shuffled_probs = pipeline.predict_proba(X_shuffled)[:, class_index]
            shuffled_brier = brier_score(list(shuffled_probs), outcomes)
            degradations.append(shuffled_brier - baseline_brier)
        importances[name] = float(np.mean(degradations))
    return importances


@dataclass(frozen=True)
class AblationResult:
    label: str
    feature_names: tuple[str, ...]
    n_eval: int
    brier_score: float
    log_loss: float
    brier_vs_elo_alone: float | None  # negative = better than Elo-alone on this same eval set


def _fit_and_score(
    tune_rows: list[MatchFeatureRow], eval_rows: list[MatchFeatureRow], feature_names: tuple[str, ...], C: float
) -> tuple[float, float]:
    config = LogisticConfig(feature_names=feature_names, C=C)
    pipeline = fit_logistic_model([r for r in tune_rows if r.has_full_history], config)
    preds = predict(pipeline, eval_rows, feature_names)
    probs = [p.home_win_probability for p in preds]
    outcomes = [p.actual_home_outcome for p in preds]
    return brier_score(probs, outcomes), log_loss_fn(probs, outcomes)


def run_feature_group_ablation(
    tune_rows: list[MatchFeatureRow],
    eval_rows: list[MatchFeatureRow],
    C: float,
    elo_alone_brier: float | None = None,
) -> list[AblationResult]:
    """Runs the experiments the Stage brief names explicitly: Elo alone,
    Elo + each named feature group, and Elo + every selected feature —
    each scored on the identical `eval_rows` set so the comparison is
    apples-to-apples. `elo_alone_brier` (if given, e.g. from the real Elo
    model rather than a feature-only proxy) is used for the "vs Elo" delta;
    otherwise it's computed from the (elo_home_win_probability) feature alone.
    """
    results: list[AblationResult] = []

    elo_only_names = (ELO_FEATURE_NAME,)
    elo_brier, elo_logloss = _fit_and_score(tune_rows, eval_rows, elo_only_names, C)
    baseline = elo_alone_brier if elo_alone_brier is not None else elo_brier
    results.append(
        AblationResult(
            label="elo_only", feature_names=elo_only_names, n_eval=len(eval_rows),
            brier_score=elo_brier, log_loss=elo_logloss, brier_vs_elo_alone=elo_brier - baseline,
        )
    )

    for group_name, group_features in FEATURE_GROUPS.items():
        names = (ELO_FEATURE_NAME, *group_features)
        b, ll = _fit_and_score(tune_rows, eval_rows, names, C)
        results.append(
            AblationResult(
                label=f"elo_plus_{group_name}", feature_names=names, n_eval=len(eval_rows),
                brier_score=b, log_loss=ll, brier_vs_elo_alone=b - baseline,
            )
        )

    all_b, all_ll = _fit_and_score(tune_rows, eval_rows, STATS_PLUS_ELO_FEATURE_NAMES, C)
    results.append(
        AblationResult(
            label="elo_plus_all_stats", feature_names=tuple(STATS_PLUS_ELO_FEATURE_NAMES), n_eval=len(eval_rows),
            brier_score=all_b, log_loss=all_ll, brier_vs_elo_alone=all_b - baseline,
        )
    )

    return results


def run_single_feature_ablation(
    tune_rows: list[MatchFeatureRow],
    eval_rows: list[MatchFeatureRow],
    full_feature_names: tuple[str, ...],
    C: float,
) -> dict[str, float]:
    """For each feature, drops it from the full feature set, refits, and
    reports the Brier change vs. the full set — positive means removing it
    made the model worse (the feature was helping); negative means removing
    it made the model *better* (the feature was net-harmful noise)."""
    full_brier, _ = _fit_and_score(tune_rows, eval_rows, full_feature_names, C)

    deltas: dict[str, float] = {}
    for name in full_feature_names:
        remaining = tuple(f for f in full_feature_names if f != name)
        if not remaining:
            continue
        without_brier, _ = _fit_and_score(tune_rows, eval_rows, remaining, C)
        deltas[name] = without_brier - full_brier
    return deltas
