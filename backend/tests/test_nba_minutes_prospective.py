"""Expected Minutes Model V1 - frozen prospective predictions and the
append-only model registry, against a real database session (also run
against PostgreSQL in CI)."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.core.prospective import FrozenRecordError
from app.models.nba import NbaEvidencePoll, NbaGame, NbaMinutesPrediction, NbaPlayer, NbaPlayerGameLog, NbaTeam, NbaTeamObservation
from app.nba.minutes import registry
from app.nba.minutes.features import ALL_FEATURES
from app.nba.minutes.models import CANDIDATES, DropEmptyColumns
from app.nba.minutes.prospective import TEAM_SOURCE_BOX_SCORE, TEAM_SOURCE_ROSTER, predict_upcoming
from app.nba.minutes.residuals import fit_uncertainty_table

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def artifact_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "MODEL_ARTIFACT_DIR", tmp_path)
    return tmp_path


def _serving_run(db):
    """A tiny but real serving run: a ridge model that predicts roughly the
    last-game minutes, plus an uncertainty table."""
    rng = np.random.default_rng(0)
    X = rng.normal(0, 1, (600, len(ALL_FEATURES)))
    last = rng.uniform(5, 40, 600)
    X[:, ALL_FEATURES.index("min_last1")] = last
    model = DropEmptyColumns(CANDIDATES["ridge"].build({"alpha": 1.0})).fit(X, last)
    path, sha = registry.save_model(model, "test-serving")
    pred = rng.uniform(1, 45, 6000)
    table = fit_uncertainty_table(pred, pred + rng.normal(0, 4, len(pred)))
    run = registry.record_run(
        db, run_key="minutes-v1-test:serving", model_name="ridge", model_version="minutes-v1-test", purpose="serving",
        dataset_cutoff=NOW - timedelta(days=150), training_seasons=[2018, 2025], evaluation_seasons=[], features=ALL_FEATURES,
        hyperparameters={"alpha": 1.0}, metrics={}, uncertainty=table, artifact_path=path, artifact_sha256=sha, code_version="test",
    )
    db.commit()
    return run


def _world(db):
    home = NbaTeam(name="Home Team", abbreviation="HOM")
    away = NbaTeam(name="Away Team", abbreviation="AWY")
    db.add_all([home, away])
    db.flush()
    vet = NbaPlayer(display_name="Veteran Player", source="espn", source_player_id="1001")
    rookie = NbaPlayer(display_name="New Player", source="espn", source_player_id="1002")
    db.add_all([vet, rookie])
    db.flush()
    start = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    for i in range(6):
        tip = start + timedelta(days=2 * i)
        g = NbaGame(
            season_start_year=2025, game_date=(tip - timedelta(hours=8)).date(), scheduled_start=tip, status="final",
            home_team_id=home.id, away_team_id=away.id, home_score=100, away_score=90, source="test", source_game_id=f"h{i}",
        )
        db.add(g)
        db.flush()
        db.add(NbaPlayerGameLog(
            player_id=vet.id, game_id=g.id, team_id=home.id, opponent_team_id=away.id, is_home=True, source="test",
            recorded_at=tip + timedelta(hours=5), minutes=30.0, started=True, did_not_play=False,
        ))
    upcoming = NbaGame(
        season_start_year=2026, game_date=NOW.date(), scheduled_start=NOW + timedelta(hours=10), status="scheduled",
        home_team_id=home.id, away_team_id=away.id, source="test", source_game_id="u1",
    )
    db.add(upcoming)
    db.commit()
    return home, away, vet, rookie, upcoming


def _roster(db, team, observed_at, source_ids):
    poll = NbaEvidencePoll(kind="rosters", source="espn", observed_at=observed_at, items_seen=1, observations_added=1)
    db.add(poll)
    db.flush()
    db.add(NbaTeamObservation(
        poll_id=poll.id, team_id=team.id, kind="roster", source="espn", observed_at=observed_at,
        payload={"players": [{"id": s, "name": s} for s in source_ids]}, content_hash=f"h{observed_at.timestamp()}{team.id}",
    ))
    db.commit()


def test_predictions_are_frozen_with_their_cutoff_before_tipoff(db_session, artifact_dir):
    _, _, vet, _, upcoming = _world(db_session)
    run = _serving_run(db_session)
    result = predict_upcoming(db_session, now=NOW, hours_ahead=24)
    assert result.games == 1 and result.written == 1
    row = db_session.query(NbaMinutesPrediction).one()
    assert row.player_id == vet.id and row.game_id == upcoming.id and row.model_run_id == run.id
    assert row.team_source == TEAM_SOURCE_BOX_SCORE  # no roster observed: last known box-score team
    cutoff = row.information_cutoff if row.information_cutoff.tzinfo else row.information_cutoff.replace(tzinfo=timezone.utc)
    assert cutoff == NOW
    assert row.inputs["min_last1"] == 30.0 and row.inputs["season_games"] == 0.0  # new season: nothing this season yet
    assert row.quantiles["p10"] <= row.expected_minutes <= row.quantiles["p90"]
    assert 20 < row.expected_minutes < 40


def test_frozen_prediction_cannot_be_rewritten(db_session, artifact_dir):
    _world(db_session)
    _serving_run(db_session)
    predict_upcoming(db_session, now=NOW, hours_ahead=24)
    row = db_session.query(NbaMinutesPrediction).one()
    row.expected_minutes = 48.0
    with pytest.raises(FrozenRecordError):
        db_session.flush()


def test_roster_observation_assigns_players_and_no_history_is_reported_not_guessed(db_session, artifact_dir):
    home, away, vet, rookie, _ = _world(db_session)
    _serving_run(db_session)
    _roster(db_session, home, NOW - timedelta(hours=3), ["1001", "1002", "9999"])
    _roster(db_session, away, NOW - timedelta(hours=3), [])
    result = predict_upcoming(db_session, now=NOW, hours_ahead=24, dry_run=True)
    assert result.skipped == {"unknown_player": 1, "no_history": 1}
    assert [r["player_id"] for r in result.rows] == [vet.id]
    assert result.rows[0]["team_source"] == TEAM_SOURCE_ROSTER
    assert db_session.query(NbaMinutesPrediction).count() == 0  # dry run writes nothing


def test_roster_observed_after_the_cutoff_is_not_used(db_session, artifact_dir):
    home, _, _, _, _ = _world(db_session)
    _serving_run(db_session)
    _roster(db_session, home, NOW + timedelta(minutes=5), ["1002"])  # learned after the cutoff
    result = predict_upcoming(db_session, now=NOW, hours_ahead=24, dry_run=True)
    assert result.rows and result.rows[0]["team_source"] == TEAM_SOURCE_BOX_SCORE


def test_a_game_still_inside_the_result_lag_is_not_history(db_session, artifact_dir):
    home, away, vet, _, _ = _world(db_session)
    _serving_run(db_session)
    tip = NOW - timedelta(hours=2)
    g = NbaGame(
        season_start_year=2026, game_date=tip.date(), scheduled_start=tip, status="final", home_team_id=home.id, away_team_id=away.id,
        home_score=100, away_score=90, source="test", source_game_id="recent",
    )
    db_session.add(g)
    db_session.flush()
    db_session.add(NbaPlayerGameLog(
        player_id=vet.id, game_id=g.id, team_id=home.id, opponent_team_id=away.id, is_home=True, source="test",
        recorded_at=tip + timedelta(hours=1), minutes=45.0, started=True, did_not_play=False,
    ))
    db_session.commit()
    early = predict_upcoming(db_session, now=NOW, hours_ahead=24, dry_run=True)
    assert early.rows[0]["expected_minutes"] < 40  # the 45-minute game is not yet known
    later = predict_upcoming(db_session, now=NOW + timedelta(hours=3), hours_ahead=24, dry_run=True)
    assert later.rows[0]["expected_minutes"] > early.rows[0]["expected_minutes"]


def test_games_that_have_tipped_off_are_not_predicted(db_session, artifact_dir):
    _, _, _, _, upcoming = _world(db_session)
    _serving_run(db_session)
    result = predict_upcoming(db_session, now=upcoming.scheduled_start + timedelta(minutes=1), hours_ahead=24, dry_run=True)
    assert result.games == 0 and result.rows == []


def test_model_file_must_match_its_recorded_hash(db_session, artifact_dir):
    run = _serving_run(db_session)
    with open(run.artifact_path, "ab") as fh:
        fh.write(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        registry.load_model(run)


def test_model_files_are_never_overwritten(artifact_dir):
    registry.save_model({"a": 1}, "same-name")
    with pytest.raises(FileExistsError):
        registry.save_model({"a": 2}, "same-name")


def test_model_runs_are_append_only(db_session, artifact_dir):
    run = _serving_run(db_session)
    run.metrics = {"mae": 0.1}
    with pytest.raises(FrozenRecordError):
        db_session.flush()
