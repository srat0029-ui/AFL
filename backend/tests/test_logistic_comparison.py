from app.backtesting.logistic_comparison import build_logistic_vs_elo_comparison
from app.modelling.logistic import LogisticPrediction


def _pred(match_id, season_year, prob, outcome) -> LogisticPrediction:
    return LogisticPrediction(match_id=match_id, season_year=season_year, home_win_probability=prob, actual_home_outcome=outcome)


def test_pairs_by_match_id():
    elo_probs = {1: 0.6, 2: 0.5}
    logistic_preds = [_pred(1, 2020, 0.62, 1.0), _pred(2, 2020, 0.48, 0.0)]
    report = build_logistic_vs_elo_comparison(elo_probs, logistic_preds)
    assert report.n_matches == 2


def test_ignores_unmatched_predictions():
    elo_probs = {1: 0.6}
    logistic_preds = [_pred(1, 2020, 0.62, 1.0), _pred(2, 2020, 0.48, 0.0)]  # match 2 has no elo probability
    report = build_logistic_vs_elo_comparison(elo_probs, logistic_preds)
    assert report.n_matches == 1


def test_disagreement_buckets_partition_all_matches():
    elo_probs = {i: 0.5 for i in range(20)}
    logistic_preds = [_pred(i, 2020, 0.5 + (i % 4) * 0.1, 1.0 if i % 2 == 0 else 0.0) for i in range(20)]
    report = build_logistic_vs_elo_comparison(elo_probs, logistic_preds)
    assert sum(b.n for b in report.disagreement_buckets) == report.n_matches


def test_empty_input_does_not_crash():
    report = build_logistic_vs_elo_comparison({}, [])
    assert report.n_matches == 0
    assert report.disagreement_buckets == []


def test_mean_disagreement_is_nonnegative():
    elo_probs = {1: 0.7, 2: 0.3}
    logistic_preds = [_pred(1, 2020, 0.5, 1.0), _pred(2, 2020, 0.6, 0.0)]
    report = build_logistic_vs_elo_comparison(elo_probs, logistic_preds)
    assert report.mean_absolute_disagreement >= 0.0
