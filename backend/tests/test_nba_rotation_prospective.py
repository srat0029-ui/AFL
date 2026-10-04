"""NBA rotation layer (V1.5): frozen prospective outlooks against a real
database session (also run against PostgreSQL in CI)."""

from datetime import timedelta

import numpy as np
import pytest

from app.core.prospective import FrozenRecordError
from app.models.nba import NbaEvidencePoll, NbaPlayerAvailabilityReport, NbaRotationPrediction
from app.nba.minutes import registry
from app.nba.minutes.prospective import latest_serving_run
from app.nba.rotation.features import PARTICIPATION_FEATURES
from app.nba.rotation.models import CLASSIFIERS, DropEmptyColumnsClassifier
from app.nba.rotation.prospective import RULE_OUT_STATUS, predict_rotation_upcoming, tier_for
from tests.test_nba_minutes_prospective import NOW, _roster, _serving_run, _world, artifact_dir  # noqa: F401  (fixture)

SIGMA = {"<10": 6.1, "10-18": 7.1, "18-24": 6.6, "24-30": 6.1, "30-34": 5.5, "34+": 5.1}


def _rotation_runs(db, *, adopted: bool = True):
    rng = np.random.default_rng(1)
    X = rng.normal(0, 1, (800, len(PARTICIPATION_FEATURES)))
    y = (X[:, PARTICIPATION_FEATURES.index("play_rate5")] > -0.8).astype(int)
    runs = {}
    for key in ("participation", "rotation_10"):
        model = DropEmptyColumnsClassifier(CLASSIFIERS["logistic"].build({"C": 1.0})).fit(X, y)
        path, sha = registry.save_model(model, f"test-{key}")
        runs[key] = registry.record_run(
            db, run_key=f"rotation-v1.0:{key}:test", model_name=f"{key}_logistic", model_version="rotation-v1.0", purpose="serving",
            dataset_cutoff=NOW - timedelta(days=150), training_seasons=[2018], evaluation_seasons=[], features=PARTICIPATION_FEATURES,
            hyperparameters={}, metrics={}, artifact_path=path, artifact_sha256=sha, code_version="test",
        )
    runs["reconciliation"] = registry.record_run(
        db, run_key="rotation-v1.0:reconciliation:test", model_name="reconciliation", model_version="rotation-v1.0", purpose="serving",
        dataset_cutoff=NOW - timedelta(days=150), training_seasons=[2018], evaluation_seasons=[], features=[],
        hyperparameters={"method": "variance", "budget": "strict_240", "alpha": 0.5, "adopted": adopted, "sigma_by_band": SIGMA, "overtime": None},
        metrics={}, code_version="test",
    )
    db.commit()
    return runs


def _report(db, player, team, observed_at, status):
    poll = NbaEvidencePoll(kind="availability", source="espn", observed_at=observed_at, items_seen=1, observations_added=1)
    db.add(poll)
    db.flush()
    db.add(NbaPlayerAvailabilityReport(
        poll_id=poll.id, player_id=player.id, team_id=team.id, observed_at=observed_at, is_listed=True,
        source_status=status.title(), status=status, source="espn",
    ))
    db.commit()


def test_outlook_keeps_every_quantity_separate_and_frozen(db_session, artifact_dir):  # noqa: F811
    _, _, vet, _, upcoming = _world(db_session)
    _serving_run(db_session)
    runs = _rotation_runs(db_session)
    result = predict_rotation_upcoming(db_session, now=NOW, hours_ahead=24)
    assert result.written == 1
    row = db_session.query(NbaRotationPrediction).one()
    assert row.player_id == vet.id and row.game_id == upcoming.id
    assert 0 <= row.p_rotation <= row.p_play <= 1  # "plays 10+" implies "plays"
    assert row.expected_minutes_if_plays is not None and row.reconciled_minutes_if_plays is not None
    assert row.participation_model_run_id == runs["participation"].id and row.reconciliation_model_run_id == runs["reconciliation"].id
    assert row.availability_evidence["state"] == "feed_not_read_by_cutoff" and row.experimental_rules == {}
    assert row.quantiles["p10"] <= row.expected_minutes_if_plays <= row.quantiles["p90"]
    row.p_play = 0.0
    with pytest.raises(FrozenRecordError):
        db_session.flush()


def test_reconciled_minutes_are_absent_when_not_adopted(db_session, artifact_dir):  # noqa: F811
    _world(db_session)
    _serving_run(db_session)
    _rotation_runs(db_session, adopted=False)
    r = predict_rotation_upcoming(db_session, now=NOW, hours_ahead=24, dry_run=True)
    assert r.rows[0]["reconciled_minutes_if_plays"] is None and r.rows[0]["expected_minutes_if_plays"] is not None


def test_new_player_gets_probabilities_but_no_invented_minutes(db_session, artifact_dir):  # noqa: F811
    home, away, _, rookie, _ = _world(db_session)
    _serving_run(db_session)
    _rotation_runs(db_session)
    _roster(db_session, home, NOW - timedelta(hours=2), ["1001", "1002"])
    _roster(db_session, away, NOW - timedelta(hours=2), [])
    r = predict_rotation_upcoming(db_session, now=NOW, hours_ahead=24, dry_run=True)
    new = next(x for x in r.rows if x["player_id"] == rookie.id)
    assert new["expected_minutes_if_plays"] is None and new["reconciled_minutes_if_plays"] is None and new["quantiles"] is None
    assert 0 <= new["p_play"] <= 1 and new["roster_listed"]
    assert r.skipped["no_history_minutes_not_estimated"] == 1


def test_roster_observed_after_cutoff_is_ignored(db_session, artifact_dir):  # noqa: F811
    home, _, _, rookie, _ = _world(db_session)
    _serving_run(db_session)
    _rotation_runs(db_session)
    _roster(db_session, home, NOW + timedelta(minutes=1), ["1002"])
    r = predict_rotation_upcoming(db_session, now=NOW, hours_ahead=24, dry_run=True)
    assert [x["player_id"] for x in r.rows] != [rookie.id] and not r.rows[0]["roster_listed"]


def test_injury_evidence_is_exposed_raw_and_never_changes_learned_fields(db_session, artifact_dir):  # noqa: F811
    home, _, vet, _, _ = _world(db_session)
    _serving_run(db_session)
    _rotation_runs(db_session)
    before = predict_rotation_upcoming(db_session, now=NOW, hours_ahead=24, dry_run=True).rows[0]
    assert before["availability_evidence"]["state"] == "feed_not_read_by_cutoff"
    _report(db_session, vet, home, NOW - timedelta(hours=1), "out")
    after = predict_rotation_upcoming(db_session, now=NOW, hours_ahead=24, dry_run=True).rows[0]
    assert after["availability_evidence"]["state"] == "listed" and after["availability_evidence"]["status"] == "out"
    assert after["experimental_rules"][RULE_OUT_STATUS]["p_play"] == 0.0
    assert after["experimental_rules"][RULE_OUT_STATUS]["applied_to_learned_fields"] is False
    for k in ("p_play", "p_rotation", "expected_minutes_if_plays", "reconciled_minutes_if_plays"):
        assert after[k] == before[k]


def test_injury_report_observed_after_cutoff_is_invisible(db_session, artifact_dir):  # noqa: F811
    home, _, vet, _, _ = _world(db_session)
    _serving_run(db_session)
    _rotation_runs(db_session)
    _report(db_session, vet, home, NOW + timedelta(minutes=10), "out")
    r = predict_rotation_upcoming(db_session, now=NOW, hours_ahead=24, dry_run=True).rows[0]
    assert r["availability_evidence"]["state"] != "listed" and r["experimental_rules"] == {}


def test_minutes_model_lookup_ignores_rotation_runs(db_session, artifact_dir):  # noqa: F811
    minutes_run = _serving_run(db_session)
    _rotation_runs(db_session)
    assert latest_serving_run(db_session).id == minutes_run.id


def test_tiers_follow_calibrated_probabilities():
    assert tier_for(0.99, 0.95) == "likely_rotation"
    assert tier_for(0.9, 0.4) == "likely_active"
    assert tier_for(0.2, 0.05) == "unlikely"
    assert tier_for(0.6, 0.3) == "uncertain"
