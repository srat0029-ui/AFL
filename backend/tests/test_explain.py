import warnings
from datetime import datetime, timezone

from app.modelling.explain import explain_prediction
from app.modelling.features import STATS_FEATURE_NAMES, MatchFeatureRow
from app.modelling.logistic import LogisticConfig, fit_logistic_model

warnings.filterwarnings("ignore", category=FutureWarning)


def _row(match_id, outcome, feature_overrides=None) -> MatchFeatureRow:
    features = {name: 0.0 for name in STATS_FEATURE_NAMES}
    if feature_overrides:
        features.update(feature_overrides)
    return MatchFeatureRow(
        match_id=match_id, season_year=2020, scheduled_start=datetime(2020, 3, 1, tzinfo=timezone.utc),
        home_team_id=1, away_team_id=2, actual_home_outcome=outcome, features=features, has_full_history=True,
    )


def _fitted_pipeline():
    import random

    rng = random.Random(0)
    rows = []
    for i in range(300):
        signal = rng.uniform(-1, 1)
        outcome = 1.0 if rng.random() < (0.5 + 0.35 * signal) else 0.0
        features = {name: rng.uniform(-1, 1) for name in STATS_FEATURE_NAMES}
        features["form_diff_5"] = signal
        rows.append(_row(i, outcome, feature_overrides=features))
    return fit_logistic_model(rows, LogisticConfig(feature_names=STATS_FEATURE_NAMES, C=1.0))


def test_explanation_has_one_contribution_per_feature():
    pipeline = _fitted_pipeline()
    row = _row(9999, 1.0, feature_overrides={"form_diff_5": 0.8})
    explanation = explain_prediction(pipeline, row, STATS_FEATURE_NAMES)
    assert len(explanation.contributions) == len(STATS_FEATURE_NAMES)
    assert {c.feature_name for c in explanation.contributions} == set(STATS_FEATURE_NAMES)


def test_contributions_sorted_by_absolute_magnitude_descending():
    pipeline = _fitted_pipeline()
    row = _row(9999, 1.0, feature_overrides={"form_diff_5": 0.9})
    explanation = explain_prediction(pipeline, row, STATS_FEATURE_NAMES)
    magnitudes = [abs(c.log_odds_contribution) for c in explanation.contributions]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_strong_positive_signal_appears_in_factors_increasing():
    pipeline = _fitted_pipeline()
    row = _row(9999, 1.0, feature_overrides={"form_diff_5": 3.0})  # far above typical range -> strong positive push
    explanation = explain_prediction(pipeline, row, STATS_FEATURE_NAMES)
    increasing_names = {c.feature_name for c in explanation.factors_increasing()}
    assert "form_diff_5" in increasing_names


def test_probability_matches_pipeline_predict_proba():
    pipeline = _fitted_pipeline()
    row = _row(9999, 1.0, feature_overrides={"form_diff_5": 0.5})
    explanation = explain_prediction(pipeline, row, STATS_FEATURE_NAMES)

    from app.modelling.logistic import predict

    direct = predict(pipeline, [row], STATS_FEATURE_NAMES)[0]
    assert explanation.home_win_probability == direct.home_win_probability


def test_to_text_contains_no_causal_language():
    pipeline = _fitted_pipeline()
    row = _row(9999, 1.0, feature_overrides={"form_diff_5": 0.8})
    explanation = explain_prediction(pipeline, row, STATS_FEATURE_NAMES)
    text = explanation.to_text()
    assert "caused" not in text.lower()
    assert "associated" in text.lower()


def test_contribution_values_are_real_model_internals_not_fabricated():
    """Manually recompute one feature's contribution from the pipeline's
    own fitted scaler/coefficients and confirm it matches exactly — proves
    explain.py isn't inventing numbers."""
    pipeline = _fitted_pipeline()
    row = _row(9999, 1.0, feature_overrides={"form_diff_5": 0.5})
    explanation = explain_prediction(pipeline, row, STATS_FEATURE_NAMES)

    from app.modelling.logistic import feature_matrix

    X = feature_matrix([row], STATS_FEATURE_NAMES)
    X_imputed = pipeline.named_steps["impute"].transform(X)
    X_scaled = pipeline.named_steps["scale"].transform(X_imputed)
    idx = STATS_FEATURE_NAMES.index("form_diff_5")
    expected_standardized = X_scaled[0][idx]
    expected_coef = pipeline.named_steps["logreg"].coef_[0][idx]

    contrib = next(c for c in explanation.contributions if c.feature_name == "form_diff_5")
    assert contrib.standardized_value == expected_standardized
    assert contrib.coefficient == expected_coef
    assert contrib.log_odds_contribution == expected_standardized * expected_coef
