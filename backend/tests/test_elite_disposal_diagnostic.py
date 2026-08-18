"""Tests for the elite disposal player monitoring diagnostic (Market
Integrity stage, Section 19) — a read-only research diagnostic over
already-persisted historical backtest predictions, bucketed by each
player's OWN historical average actual disposals (ground truth), never
reputation. Must never touch the promoted model."""

from datetime import datetime, timezone

from app.models import Player, PlayerDisposalPrediction, PlayerModelRun
from app.player_modelling.elite_disposal_diagnostic import (
    BUCKET_ELITE,
    BUCKET_LOW,
    load_elite_disposal_diagnostic,
)
from app.player_modelling.market import PlayerMarket

NOW = datetime.now(timezone.utc)


def _seed_promoted_model_run(db):
    run = PlayerModelRun(
        model_name="disposals_ridge", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
        evaluation_end_year=2025, is_promoted=True, run_at=NOW,
    )
    db.add(run)
    db.commit()
    return run


def _seed_player(db, name):
    player = Player(sport_id=1, display_name=name, source="afltables", source_player_id=name)
    db.add(player)
    db.flush()
    return player


def _seed_prediction(db, run, player, match_id, *, predicted_mean, actual):
    db.add(
        PlayerDisposalPrediction(
            model_run_id=run.id, player_id=player.id, match_id=match_id, team_id=1, season_year=2020,
            games_of_history=20, predicted_mean=predicted_mean, nb_alpha=3.0, actual_disposals=actual,
        )
    )


def test_returns_none_without_promoted_model(db_session):
    assert load_elite_disposal_diagnostic(db_session) is None


def test_returns_none_without_any_predictions(db_session):
    _seed_promoted_model_run(db_session)
    assert load_elite_disposal_diagnostic(db_session) is None


def test_buckets_by_players_own_historical_actual_average_not_reputation(db_session):
    run = _seed_promoted_model_run(db_session)
    elite_player = _seed_player(db_session, "Elite Player")
    low_player = _seed_player(db_session, "Low Player")
    for i, (predicted, actual) in enumerate([(27.0, 29.0), (26.0, 30.0), (28.0, 27.0)]):
        _seed_prediction(db_session, run, elite_player, match_id=100 + i, predicted_mean=predicted, actual=actual)
    for i, (predicted, actual) in enumerate([(11.0, 10.0), (12.0, 11.0)]):
        _seed_prediction(db_session, run, low_player, match_id=200 + i, predicted_mean=predicted, actual=actual)
    db_session.commit()

    buckets = load_elite_disposal_diagnostic(db_session)
    assert buckets is not None
    by_bucket = {b.bucket: b for b in buckets}
    assert BUCKET_ELITE in by_bucket
    assert BUCKET_LOW in by_bucket
    assert by_bucket[BUCKET_ELITE].n_players == 1
    assert by_bucket[BUCKET_ELITE].n_predictions == 3


def test_negative_bias_means_model_under_predicts(db_session):
    run = _seed_promoted_model_run(db_session)
    player = _seed_player(db_session, "Consistently Under-predicted")
    for i in range(5):
        _seed_prediction(db_session, run, player, match_id=300 + i, predicted_mean=25.0, actual=30.0)
    db_session.commit()

    buckets = load_elite_disposal_diagnostic(db_session)
    bucket = next(b for b in buckets if b.n_players == 1)
    assert bucket.bias < 0
    assert bucket.avg_predicted < bucket.avg_actual


def test_most_under_predicted_players_sorted_most_negative_first(db_session):
    run = _seed_promoted_model_run(db_session)
    worse = _seed_player(db_session, "Worse Bias")
    better = _seed_player(db_session, "Better Bias")
    for i in range(3):
        _seed_prediction(db_session, run, worse, match_id=400 + i, predicted_mean=24.0, actual=30.0)  # bias -6
    for i in range(3):
        _seed_prediction(db_session, run, better, match_id=500 + i, predicted_mean=28.0, actual=30.0)  # bias -2
    db_session.commit()

    buckets = load_elite_disposal_diagnostic(db_session)
    bucket = next(b for b in buckets if b.n_players == 2)
    names_in_order = [p.player_name for p in bucket.most_under_predicted_players]
    assert names_in_order[0] == "Worse Bias"


def test_only_uses_promoted_model_run(db_session):
    unpromoted = PlayerModelRun(
        model_name="disposals_experimental", market=PlayerMarket.DISPOSALS.value, feature_names=[], config_json={},
        distribution_method="nb", tune_start_year=2016, tune_end_year=2018, evaluation_start_year=2019,
        evaluation_end_year=2025, is_promoted=False, run_at=NOW,
    )
    db_session.add(unpromoted)
    db_session.commit()
    player = _seed_player(db_session, "Some Player")
    _seed_prediction(db_session, unpromoted, player, match_id=600, predicted_mean=20.0, actual=20.0)
    db_session.commit()

    # No promoted run exists - must not silently fall back to an unpromoted one.
    assert load_elite_disposal_diagnostic(db_session) is None
