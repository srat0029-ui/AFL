import random
from datetime import datetime, timedelta, timezone

from app.modelling.boosting import BoostingConfig, fit_boosting_model
from app.modelling.boosting_interactions import HYPOTHESIS_PAIRS, analyze_interactions, supports_interactions
from app.modelling.features import STATS_PLUS_ELO_FEATURE_NAMES, MatchFeatureRow


def _row(match_id, season_year, outcome, feature_overrides=None) -> MatchFeatureRow:
    features = {name: 0.0 for name in STATS_PLUS_ELO_FEATURE_NAMES}
    if feature_overrides:
        features.update(feature_overrides)
    return MatchFeatureRow(
        match_id=match_id, season_year=season_year,
        scheduled_start=datetime(season_year, 3, 1, tzinfo=timezone.utc) + timedelta(days=match_id),
        home_team_id=1, away_team_id=2, actual_home_outcome=outcome, features=features, has_full_history=True,
    )


def _synthetic_rows_with_interaction(n, seed=0):
    """Outcome depends on the PRODUCT of two features — a genuine
    interaction a tree model can pick up but a linear model can't."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        elo = rng.uniform(-1, 1)
        inside50 = rng.uniform(-1, 1)
        interaction_signal = elo * inside50  # only large when BOTH are large and same-signed
        outcome = 1.0 if rng.random() < (0.5 + 0.4 * interaction_signal) else 0.0
        features = {name: rng.uniform(-1, 1) for name in STATS_PLUS_ELO_FEATURE_NAMES}
        features["elo_home_win_probability"] = elo
        features["inside50_differential_diff"] = inside50
        rows.append(_row(i, 2016, outcome, feature_overrides=features))
    return rows


def test_supports_interactions_true_for_xgboost():
    rows = _synthetic_rows_with_interaction(50)
    model = fit_boosting_model(rows, BoostingConfig(library="xgboost", feature_names=STATS_PLUS_ELO_FEATURE_NAMES))
    assert supports_interactions(model) is True


def test_supports_interactions_false_for_lightgbm():
    rows = _synthetic_rows_with_interaction(50)
    model = fit_boosting_model(rows, BoostingConfig(library="lightgbm", feature_names=STATS_PLUS_ELO_FEATURE_NAMES))
    assert supports_interactions(model) is False


def test_analyze_interactions_returns_empty_for_unsupported_library():
    rows = _synthetic_rows_with_interaction(50)
    model = fit_boosting_model(rows, BoostingConfig(library="lightgbm", feature_names=STATS_PLUS_ELO_FEATURE_NAMES))
    findings = analyze_interactions(model, rows, STATS_PLUS_ELO_FEATURE_NAMES)
    assert findings == []


def test_analyze_interactions_detects_a_real_interaction():
    rows = _synthetic_rows_with_interaction(500, seed=1)
    config = BoostingConfig(library="xgboost", feature_names=STATS_PLUS_ELO_FEATURE_NAMES, max_depth=3, n_estimators=100)
    model = fit_boosting_model(rows, config)

    findings = analyze_interactions(model, rows, STATS_PLUS_ELO_FEATURE_NAMES, top_n=5)

    assert len(findings) > 0
    top_pair_features = {(f.feature_a, f.feature_b) for f in findings[:5]}
    expected_pair = {"elo_home_win_probability", "inside50_differential_diff"}
    assert any(set(pair) == expected_pair for pair in top_pair_features)


def test_analyze_interactions_includes_hypothesis_pairs_even_if_weak():
    rows = _synthetic_rows_with_interaction(200, seed=2)
    model = fit_boosting_model(rows, BoostingConfig(library="xgboost", feature_names=STATS_PLUS_ELO_FEATURE_NAMES))
    findings = analyze_interactions(model, rows, STATS_PLUS_ELO_FEATURE_NAMES, top_n=1)
    labels = {(f.feature_a, f.feature_b) for f in findings}
    hypothesis_columns = {(a, b) for a, b, _ in HYPOTHESIS_PAIRS}
    # at least the elo/inside50 hypothesis pair should be present given top_n=1 forces the hypothesis-append path
    assert any(pair in labels or (pair[1], pair[0]) in labels for pair in hypothesis_columns)


def test_analyze_interactions_empty_rows_returns_empty():
    rows = _synthetic_rows_with_interaction(50)
    model = fit_boosting_model(rows, BoostingConfig(library="xgboost", feature_names=STATS_PLUS_ELO_FEATURE_NAMES))
    assert analyze_interactions(model, [], STATS_PLUS_ELO_FEATURE_NAMES) == []
