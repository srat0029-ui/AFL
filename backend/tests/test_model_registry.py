"""Tests for the Model Registry (status classification + append-only
promotion audit trail): champion/previous-champion/rejected derivation,
promotion events never mutating history, and the disposal Ridge-vs-Huber
head-to-head surfacing frozen evidence rather than re-deriving it."""

from datetime import datetime, timedelta, timezone

from app.models import ModelRun
from app.player_modelling.disposal_backtest import build_dataset_from_rows, run_candidate_models
from app.player_modelling.disposal_data import PlayerGameRow
from app.player_modelling.disposal_evaluation import evaluate_model
from app.player_modelling.disposal_persistence import persist_model_run
from app.player_modelling.model_registry import (
    STATUS_CHAMPION,
    STATUS_PREVIOUS_CHAMPION,
    STATUS_REJECTED,
    disposal_ridge_vs_huber,
    list_disposal_models,
    list_promotion_events,
    list_team_models,
    record_promotion_event,
)

BASE_2018 = datetime(2018, 4, 1, tzinfo=timezone.utc)
BASE_2019 = datetime(2019, 4, 1, tzinfo=timezone.utc)


def _row(player_id, match_id, season_year, when, disposals):
    return PlayerGameRow(
        player_id=player_id, match_id=match_id, team_id=10, opponent_team_id=20, season_year=season_year,
        round_number=1, is_final=False, is_home=True, venue_id=1, scheduled_start=when, disposals=disposals,
        kicks=8, handballs=7, marks=2, tackles=3, clearances=1, inside_50s=2, contested_possessions=5,
        uncontested_possessions=6, time_on_ground_pct=80, subbed_on=False, subbed_off=False,
    )


def _synthetic_rows():
    rows = []
    match_id = 1
    for p in range(1, 8):
        for g in range(8):
            season_year = 2018 if g < 4 else 2019
            base = BASE_2018 if season_year == 2018 else BASE_2019
            rows.append(_row(p, match_id, season_year, base + timedelta(days=7 * g), disposals=10 + (p + g) % 15))
            match_id += 1
    return rows


def _persist(db, name, is_promoted):
    split = build_dataset_from_rows(_synthetic_rows(), team_rows=[], team_context={})
    preds = run_candidate_models(split, model_names=("ridge",))["ridge"]
    ev = evaluate_model(name, preds, "nb")
    return persist_model_run(
        db, model_name=name, feature_names=(), config={}, distribution_method="nb",
        tune_start_year=2016, tune_end_year=2018, evaluation=ev, predictions=preds, is_promoted=is_promoted,
    )


def test_promoted_run_is_champion(db_session):
    _persist(db_session, "disposals_ridge", is_promoted=True)
    models = {m.model_name: m for m in list_disposal_models(db_session)}
    assert models["disposals_ridge"].status == STATUS_CHAMPION


def test_never_promoted_run_is_rejected(db_session):
    _persist(db_session, "disposals_ridge", is_promoted=True)
    _persist(db_session, "disposals_negative_binomial", is_promoted=False)
    models = {m.model_name: m for m in list_disposal_models(db_session)}
    assert models["disposals_negative_binomial"].status == STATUS_REJECTED


def test_dethroned_champion_reads_as_previous_champion_not_rejected(db_session):
    ridge_run = _persist(db_session, "disposals_ridge", is_promoted=True)
    huber_run = _persist(db_session, "disposals_huber", is_promoted=False)
    huber_run.is_promoted = True
    ridge_run.is_promoted = False
    db_session.commit()

    record_promotion_event(
        db_session, market="player_disposals", previous_champion_model_name="disposals_ridge",
        previous_champion_model_version="disposals_ridge@x", new_champion_model_name="disposals_huber",
        new_champion_model_version="disposals_huber@y", promoted_at=datetime.now(timezone.utc),
        evidence_summary="test evidence", evaluation_metrics={"a": 1},
    )

    models = {m.model_name: m for m in list_disposal_models(db_session)}
    assert models["disposals_ridge"].status == STATUS_PREVIOUS_CHAMPION
    assert models["disposals_huber"].status == STATUS_CHAMPION


def test_promotion_events_are_append_only_and_never_edited(db_session):
    e1 = record_promotion_event(
        db_session, market="player_disposals", previous_champion_model_name=None, previous_champion_model_version=None,
        new_champion_model_name="disposals_ridge", new_champion_model_version="disposals_ridge@1",
        promoted_at=datetime(2026, 1, 1, tzinfo=timezone.utc), evidence_summary="first", evaluation_metrics={},
    )
    e2 = record_promotion_event(
        db_session, market="player_disposals", previous_champion_model_name="disposals_ridge",
        previous_champion_model_version="disposals_ridge@1", new_champion_model_name="disposals_huber",
        new_champion_model_version="disposals_huber@2", promoted_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        evidence_summary="second", evaluation_metrics={},
    )
    events = list_promotion_events(db_session, market="player_disposals")
    assert len(events) == 2
    assert events[0].evidence_summary == "second"  # newest first
    assert events[1].evidence_summary == "first"
    assert e1.id != e2.id  # two rows, first never overwritten


def test_only_two_dethronings_ago_champion_is_previous_not_the_original(db_session):
    """A champion from TWO promotions ago must read as Rejected, not
    Previous Champion — only the most recent hand-off counts as "previous"."""
    _persist(db_session, "disposals_baseline_last5", is_promoted=False)
    ridge_run = _persist(db_session, "disposals_ridge", is_promoted=True)
    huber_run = _persist(db_session, "disposals_huber", is_promoted=False)

    record_promotion_event(
        db_session, market="player_disposals", previous_champion_model_name="disposals_baseline_last5",
        previous_champion_model_version="x", new_champion_model_name="disposals_ridge", new_champion_model_version="y",
        promoted_at=datetime(2026, 1, 1, tzinfo=timezone.utc), evidence_summary="e1", evaluation_metrics={},
    )
    ridge_run.is_promoted, huber_run.is_promoted = False, True
    db_session.commit()
    record_promotion_event(
        db_session, market="player_disposals", previous_champion_model_name="disposals_ridge",
        previous_champion_model_version="y", new_champion_model_name="disposals_huber", new_champion_model_version="z",
        promoted_at=datetime(2026, 2, 1, tzinfo=timezone.utc), evidence_summary="e2", evaluation_metrics={},
    )

    models = {m.model_name: m for m in list_disposal_models(db_session)}
    assert models["disposals_baseline_last5"].status == STATUS_REJECTED  # 2 promotions ago
    assert models["disposals_ridge"].status == STATUS_PREVIOUS_CHAMPION  # most recent hand-off
    assert models["disposals_huber"].status == STATUS_CHAMPION


def test_disposal_head_to_head_surfaces_frozen_bias_from_promotion_event(db_session):
    _persist(db_session, "disposals_ridge", is_promoted=False)
    _persist(db_session, "disposals_huber", is_promoted=True)
    record_promotion_event(
        db_session, market="player_disposals", previous_champion_model_name="disposals_ridge",
        previous_champion_model_version="x", new_champion_model_name="disposals_huber", new_champion_model_version="y",
        promoted_at=datetime.now(timezone.utc), evidence_summary="e",
        evaluation_metrics={
            "ridge_high_volume_bias": {"28+": -0.42}, "huber_high_volume_bias": {"28+": -0.307},
            "ridge_low_history_bias": {"<5": 1.7}, "huber_low_history_bias": {"<5": 1.08},
        },
    )
    h2h = disposal_ridge_vs_huber(db_session)
    assert h2h.ridge.model_name == "disposals_ridge"
    assert h2h.huber.model_name == "disposals_huber"
    assert h2h.huber_high_volume_bias == {"28+": -0.307}
    assert h2h.ridge_low_history_bias == {"<5": 1.7}


def test_team_models_only_elo_and_poisson_are_champion(db_session):
    now = datetime.now(timezone.utc)
    for name in ("elo", "poisson", "boosting", "logistic_stats_only"):
        db_session.add(ModelRun(model_name=name, config_json={}, tune_end_year=2022, run_at=now))
    db_session.commit()

    models = {m.model_name: m for m in list_team_models(db_session)}
    assert models["elo"].status == STATUS_CHAMPION
    assert models["poisson"].status == STATUS_CHAMPION
    assert models["boosting"].status == STATUS_REJECTED
    assert models["logistic_stats_only"].status == STATUS_REJECTED
