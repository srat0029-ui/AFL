from dataclasses import dataclass

from sqlalchemy import select

from app.modelling.model_run_persistence import persist_model_run
from app.models import ModelRun, ModelValidationMetric


@dataclass(frozen=True)
class _FakeConfig:
    k_factor: float = 32.0
    home_advantage: float = 35.0


def test_persist_creates_run_and_metrics(db_session):
    run = persist_model_run(
        db_session,
        model_name="elo",
        config=_FakeConfig(),
        tune_end_year=2022,
        metrics=[
            {
                "market_type": "h2h",
                "metric_name": "brier_score",
                "holdout_n": 648,
                "holdout_value": 0.2012,
                "naive_baseline_value": 0.25,
                "has_edge_over_naive": True,
            }
        ],
    )

    assert run.model_name == "elo"
    assert run.config_json == {"k_factor": 32.0, "home_advantage": 35.0}
    assert len(run.metrics) == 1
    assert run.metrics[0].holdout_value == 0.2012
    assert run.metrics[0].has_edge_over_naive is True


def test_persist_is_upsert_not_additive(db_session):
    persist_model_run(
        db_session, "elo", _FakeConfig(), 2022,
        [{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 100,
          "holdout_value": 0.22, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )
    persist_model_run(
        db_session, "elo", _FakeConfig(k_factor=15.0), 2021,
        [{"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 200,
          "holdout_value": 0.19, "naive_baseline_value": 0.25, "has_edge_over_naive": True}],
    )

    runs = db_session.scalars(select(ModelRun)).all()
    assert len(runs) == 1
    assert runs[0].config_json["k_factor"] == 15.0
    assert runs[0].tune_end_year == 2021

    metrics = db_session.scalars(select(ModelValidationMetric)).all()
    assert len(metrics) == 1
    assert metrics[0].holdout_value == 0.19


def test_persist_multiple_markets_for_poisson(db_session):
    run = persist_model_run(
        db_session, "poisson", _FakeConfig(), 2022,
        metrics=[
            {"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 648,
             "holdout_value": 0.2052, "naive_baseline_value": 0.25, "has_edge_over_naive": True},
            {"market_type": "line", "metric_name": "mae", "holdout_n": 648,
             "holdout_value": 27.20, "naive_baseline_value": 31.29, "has_edge_over_naive": True},
            {"market_type": "total", "metric_name": "mae", "holdout_n": 648,
             "holdout_value": 23.47, "naive_baseline_value": 23.52, "has_edge_over_naive": False},
        ],
    )

    assert len(run.metrics) == 3
    by_market = {m.market_type: m for m in run.metrics}
    assert by_market["total"].has_edge_over_naive is False
    assert by_market["line"].has_edge_over_naive is True


def test_persist_replacing_market_set_removes_stale_markets(db_session):
    persist_model_run(
        db_session, "poisson", _FakeConfig(), 2022,
        metrics=[
            {"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 1,
             "holdout_value": 0.2, "naive_baseline_value": 0.25, "has_edge_over_naive": True},
            {"market_type": "total", "metric_name": "mae", "holdout_n": 1,
             "holdout_value": 23.0, "naive_baseline_value": 23.5, "has_edge_over_naive": False},
        ],
    )
    # rerun with only one market this time
    persist_model_run(
        db_session, "poisson", _FakeConfig(), 2022,
        metrics=[
            {"market_type": "h2h", "metric_name": "brier_score", "holdout_n": 1,
             "holdout_value": 0.19, "naive_baseline_value": 0.25, "has_edge_over_naive": True},
        ],
    )

    metrics = db_session.scalars(select(ModelValidationMetric)).all()
    assert len(metrics) == 1
    assert metrics[0].market_type == "h2h"
