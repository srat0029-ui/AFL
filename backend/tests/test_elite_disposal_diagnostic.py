"""Tests for the elite disposal player monitoring diagnostic (Market
Integrity stage, Section 19) — a read-only research diagnostic over
already-persisted historical backtest predictions, bucketed by each
player's OWN historical average actual disposals (ground truth), never
reputation. Must never touch the promoted model."""

from datetime import datetime, timezone

from app.models import Match, MatchStatus, Player, PlayerDisposalPrediction, PlayerMatchStat, PlayerModelRun, Round, Season, Sport, Team
from app.player_modelling.elite_disposal_diagnostic import (
    BUCKET_ELITE,
    BUCKET_LOW,
    load_elite_disposal_diagnostic,
)
from app.player_modelling.market import PlayerMarket

NOW = datetime.now(timezone.utc)
CURRENT_YEAR = NOW.year


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

    # current_only=False, min_n_predictions=None: this test is about bias-
    # sort ordering, not current-player or sample-size scoping — these
    # synthetic players have only 3 predictions each and no current-season
    # signal, so the defaults would (correctly) filter them out entirely.
    buckets = load_elite_disposal_diagnostic(db_session, current_only=False, min_n_predictions=None)
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


def test_current_only_filters_display_list_but_never_the_bucket_aggregates(db_session):
    """Data-scoping fix: a retired player (historical predictions only, no
    current-season match/lineup/projection) must disappear from the
    displayed player list under the current_only=True default, but the
    bucket's own historical aggregate metrics — n_players, n_predictions,
    avg_actual, avg_predicted, bias, mae — must be BYTE-for-byte identical
    to the current_only=False (full historical population) view."""
    sport = Sport(code="AFL", name="Australian Football League")
    db_session.add(sport)
    db_session.flush()
    season = Season(sport_id=sport.id, year=CURRENT_YEAR)
    db_session.add(season)
    db_session.flush()
    round_ = Round(season_id=season.id, round_number=1)
    home = Team(sport_id=sport.id, name="Melbourne", short_name="MEL")
    away = Team(sport_id=sport.id, name="Carlton", short_name="CAR")
    db_session.add_all([round_, home, away])
    db_session.flush()
    current_match = Match(
        sport_id=sport.id, season_id=season.id, round_id=round_.id, home_team_id=home.id, away_team_id=away.id,
        scheduled_start=datetime(CURRENT_YEAR, 4, 1, tzinfo=timezone.utc), status=MatchStatus.SCHEDULED,
    )
    db_session.add(current_match)
    db_session.flush()

    run = _seed_promoted_model_run(db_session)
    retired = Player(sport_id=sport.id, display_name="Retired Elite", source="afltables", source_player_id="p_retired_elite")
    current = Player(sport_id=sport.id, display_name="Current Elite", current_team_id=home.id, source="afltables", source_player_id="p_current_elite")
    db_session.add_all([retired, current])
    db_session.flush()
    # `current` actually played in the current season - the retired player never did.
    db_session.add(PlayerMatchStat(
        player_id=current.id, match_id=current_match.id, team_id=home.id, source="afltables", recorded_at=NOW, disposals=30,
    ))
    for i, (predicted, actual) in enumerate([(27.0, 30.0), (26.0, 31.0)]):
        _seed_prediction(db_session, run, retired, match_id=700 + i, predicted_mean=predicted, actual=actual)
    for i, (predicted, actual) in enumerate([(28.0, 29.0), (29.0, 30.0)]):
        _seed_prediction(db_session, run, current, match_id=800 + i, predicted_mean=predicted, actual=actual)
    db_session.commit()

    # min_n_predictions=None: this test isolates current-player scoping —
    # each player here only has 2 predictions, below the 20+ default.
    all_buckets = {b.bucket: b for b in load_elite_disposal_diagnostic(db_session, current_only=False, min_n_predictions=None)}
    current_buckets = {b.bucket: b for b in load_elite_disposal_diagnostic(db_session, current_only=True, min_n_predictions=None)}

    elite_all = all_buckets[BUCKET_ELITE]
    elite_current = current_buckets[BUCKET_ELITE]
    assert elite_all.n_players == elite_current.n_players == 2
    assert elite_all.n_predictions == elite_current.n_predictions == 4
    assert elite_all.avg_actual == elite_current.avg_actual
    assert elite_all.avg_predicted == elite_current.avg_predicted
    assert elite_all.bias == elite_current.bias
    assert elite_all.mae == elite_current.mae

    names_all = {p.player_name for p in elite_all.most_under_predicted_players}
    names_current = {p.player_name for p in elite_current.most_under_predicted_players}
    assert names_all == {"Retired Elite", "Current Elite"}
    assert names_current == {"Current Elite"}


def test_min_n_predictions_filters_display_list_only(db_session):
    run = _seed_promoted_model_run(db_session)
    small_sample = _seed_player(db_session, "Small Sample")
    big_sample = _seed_player(db_session, "Big Sample")
    for i in range(3):  # below the default 20+ threshold
        _seed_prediction(db_session, run, small_sample, match_id=900 + i, predicted_mean=27.0, actual=29.0)
    for i in range(25):  # above it
        _seed_prediction(db_session, run, big_sample, match_id=1000 + i, predicted_mean=27.0, actual=29.0)
    db_session.commit()

    unfiltered = {b.bucket: b for b in load_elite_disposal_diagnostic(db_session, current_only=False, min_n_predictions=None)}
    default_filtered = {b.bucket: b for b in load_elite_disposal_diagnostic(db_session, current_only=False)}  # default min_n_predictions=20

    elite_unfiltered = unfiltered[BUCKET_ELITE]
    elite_default = default_filtered[BUCKET_ELITE]

    # Bucket aggregates identical regardless of the display-only filter.
    assert elite_unfiltered.n_players == elite_default.n_players == 2
    assert elite_unfiltered.n_predictions == elite_default.n_predictions == 28
    assert elite_unfiltered.avg_actual == elite_default.avg_actual
    assert elite_unfiltered.bias == elite_default.bias
    assert elite_unfiltered.mae == elite_default.mae

    assert {p.player_name for p in elite_unfiltered.most_under_predicted_players} == {"Small Sample", "Big Sample"}
    assert {p.player_name for p in elite_default.most_under_predicted_players} == {"Big Sample"}
