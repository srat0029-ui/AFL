from app.modelling.elo import EloConfig
from app.modelling.logistic_cli import PersistedLogisticConfig
from app.modelling.model_run_persistence import persist_model_run
from app.modelling.poisson_model import PoissonConfig
from tests.test_logistic_report import _seed_full_dataset, _seed_model_runs


def _seed_logistic_runs(db_session):
    from app.modelling.features import STATS_FEATURE_NAMES, STATS_PLUS_ELO_FEATURE_NAMES

    for name, feature_set, features in [
        ("logistic_stats_only", "stats_only", STATS_FEATURE_NAMES),
        ("logistic_stats_plus_elo", "stats_plus_elo", STATS_PLUS_ELO_FEATURE_NAMES),
    ]:
        config = PersistedLogisticConfig(
            feature_set=feature_set, feature_names=features, C=1.0, random_state=42,
            form_window_short=5, form_window_long=10, stats_window=6,
            tune_start_year=2016, tune_end_year=2018, inner_validation_start_year=2018,
            evaluation_start_year=2019,
        )
        persist_model_run(db_session, name, config, tune_end_year=2018, metrics=[])


def test_logistic_comparison_503_when_not_run(client, db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    response = client.get("/api/backtests/logistic-comparison")
    assert response.status_code == 503


def test_logistic_comparison_returns_full_structure(client, db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    _seed_logistic_runs(db_session)

    response = client.get("/api/backtests/logistic-comparison")

    assert response.status_code == 200
    body = response.json()
    assert body["n_eval"] > 0
    assert len(body["baselines"]) == 3
    assert "elo" in body
    assert "poisson" in body
    assert body["stats_only"]["variant"] == "stats_only"
    assert body["stats_plus_elo"]["variant"] == "stats_plus_elo"
    assert "promotion" in body["stats_only"]
    assert "feature_group_ablation" in body["stats_only"]
    assert "standardized_coefficients" in body["stats_only"]
    assert "disagreement_vs_elo" in body["stats_only"]


def test_logistic_comparison_reads_persisted_C(client, db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    _seed_logistic_runs(db_session)

    response = client.get("/api/backtests/logistic-comparison")
    body = response.json()
    assert body["stats_only"]["C"] == 1.0
    assert body["stats_plus_elo"]["C"] == 1.0


def test_logistic_comparison_route_does_not_shadow_model_id_route(client, db_session):
    _seed_full_dataset(db_session)
    _seed_model_runs(db_session)
    response = client.get("/api/backtests/elo")
    assert response.status_code == 200
    assert response.json()["model_name"] == "elo"
