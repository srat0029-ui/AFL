import warnings

import pytest

from app.backtesting.boosting_report import FEATURE_SETS, ModelsUnavailableError, build_boosting_comparison
from tests.test_logistic_report import _seed_full_dataset, _seed_model_runs

warnings.filterwarnings("ignore", category=FutureWarning)


def test_raises_when_elo_not_run(db_session):
    _seed_full_dataset(db_session)
    with pytest.raises(ModelsUnavailableError):
        build_boosting_comparison(db_session)


def test_build_boosting_comparison_produces_structured_report(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)

    overview = build_boosting_comparison(db_session)

    assert overview.n_eval > 0
    assert overview.evaluation_start_year == 2019
    assert len(overview.feature_set_candidates) == len(FEATURE_SETS) * 2  # 2 libraries
    assert overview.best.n_eval == overview.n_eval


def test_all_candidates_scored_on_identical_match_count(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    overview = build_boosting_comparison(db_session)
    ns = {c.n_eval for c in overview.feature_set_candidates}
    assert ns == {overview.n_eval}


def test_best_candidate_is_the_lowest_brier_among_candidates(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    overview = build_boosting_comparison(db_session)
    lowest_raw = min(c.brier_score for c in overview.feature_set_candidates)
    matching = [c for c in overview.feature_set_candidates if c.brier_score == lowest_raw]
    assert overview.best.label == matching[0].label
    assert overview.best.library == matching[0].library


def test_promotion_decision_present(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    overview = build_boosting_comparison(db_session)
    assert isinstance(overview.best.promotion.promote, bool)
    assert len(overview.best.promotion.reasons) == 5


def test_disagreement_and_ablation_present(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    overview = build_boosting_comparison(db_session)
    assert overview.best.disagreement_vs_elo.n_matches == overview.n_eval
    labels = {a.label for a in overview.best.feature_group_ablation}
    assert "elo_only" in labels
    assert "elo_plus_all_stats" in labels


def test_ensemble_report_present(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    overview = build_boosting_comparison(db_session)
    assert 0.0 <= overview.ensemble.boosting_weight <= 1.0
    assert isinstance(overview.ensemble.use_ensemble, bool)


def test_permutation_importance_covers_best_feature_set(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    overview = build_boosting_comparison(db_session)
    assert set(overview.best.permutation_importance.keys()) == set(overview.best.feature_names)


def test_deterministic_rerun(db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    overview_1 = build_boosting_comparison(db_session)
    overview_2 = build_boosting_comparison(db_session)
    assert overview_1.best.brier_score == overview_2.best.brier_score
    assert overview_1.best.label == overview_2.best.label
    assert overview_1.ensemble.boosting_weight == overview_2.ensemble.boosting_weight
