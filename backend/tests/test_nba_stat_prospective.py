"""Frozen Stat Model V1 predictions against a real database session (also run
against PostgreSQL in CI): timing, immutability, isolation from V1.5, the
validation labels and scoring of the exact frozen rows."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.core.prospective import FrozenRecordError, ensure_utc
from app.models.nba import NbaGame, NbaOutlookSnapshot, NbaPlayerGameLog, NbaPropProjection, NbaRotationPrediction
from app.nba import stats as stats_package
from app.nba.outlooks.snapshots import SnapshotSpec, run_due_snapshots
from app.nba.stats import models as sm
from app.nba.stats import prospective as sp
from app.nba.stats.prospective import FREEZE_LABELS, VALIDATION, NbBucketDistribution, evaluate_frozen, freeze_due, load_parameters
from tests.test_nba_minutes_prospective import artifact_dir  # noqa: F401  (fixture)
from tests.test_nba_outlook_snapshots import TIP, _setup

REPO = Path(stats_package.__file__).resolve().parents[4]
T4H = SnapshotSpec("T-4h", timedelta(hours=4))
T1H = SnapshotSpec("T-1h", timedelta(hours=1))
AT_T4H = TIP - timedelta(hours=4)


def _world_with_stats(db):
    out = _setup(db, rosters_at=TIP - timedelta(hours=30))
    for i, log in enumerate(db.query(NbaPlayerGameLog).all()):
        log.points, log.rebounds, log.assists = 10 + i, 4, 3
    db.commit()
    return out


def _snapshot(db, spec=T4H, now=AT_T4H):
    run_due_snapshots(db, now=now, specs=(spec,))


def _v15_state(db):
    snaps = [(s.id, s.status, s.generated_at) for s in db.query(NbaOutlookSnapshot).order_by(NbaOutlookSnapshot.id)]
    rows = [(r.id, r.p_play, r.p_rotation, r.expected_minutes_if_plays) for r in db.query(NbaRotationPrediction).order_by(NbaRotationPrediction.id)]
    return snaps, rows


def test_parameters_are_the_recorded_test_fold_values():
    params, _ = load_parameters()
    report = json.loads((REPO / params["source_report"]).read_text(encoding="utf-8"))
    for stat, p in params["stats"].items():
        fold = report["stats"][stat]["fold_parameters"]["2025"]
        assert (p["halflife"], p["shrink"], p["prior_rates"]) == (fold["halflife"], fold["shrink"], fold["prior_rates"])
        assert p["alpha_buckets"] == report["stats"][stat]["alphas_latest_fold"]["bucket"]


def test_validation_labels_match_the_recorded_decisions():
    params, _ = load_parameters()
    report = json.loads((REPO / params["source_report"]).read_text(encoding="utf-8"))
    for stat, status in VALIDATION.items():
        decision = report["stats"][stat]["decision"]
        assert status["calibrated"] is decision["distribution_adopted"]
        assert (status["distribution"] == "passed_offline") is decision["distribution_adopted"]
        assert (status["mean"] == "passed_offline") is decision["mean_adopted"]
    assert VALIDATION["points"]["calibrated"] is False


def test_freezes_all_three_stats_with_cutoff_snapshot_and_baselines(db_session, artifact_dir):  # noqa: F811
    _, _, vet, _, game, _ = _world_with_stats(db_session)
    _snapshot(db_session)
    snap = db_session.query(NbaOutlookSnapshot).one()
    now = AT_T4H + timedelta(minutes=2)
    result = freeze_due(db_session, now=now)
    assert len(result.frozen) == 1 and not result.failed
    rows = db_session.query(NbaPropProjection).filter_by(player_id=vet.id, game_id=game.id).all()
    assert {r.market for r in rows} == {"player_points", "player_rebounds", "player_assists"}
    outlook = db_session.query(NbaRotationPrediction).filter_by(player_id=vet.id, snapshot_id=snap.id).one()
    for r in rows:
        assert ensure_utc(r.information_cutoff) == ensure_utc(snap.information_cutoff) and ensure_utc(r.generated_at) == now
        assert r.expected_minutes == outlook.expected_minutes_if_plays and r.predicted_mean == pytest.approx(r.expected_minutes * r.rate_per_minute)
        assert r.inputs["snapshot_id"] == snap.id and r.inputs["rotation_prediction_id"] == outlook.id and r.inputs["parameters_sha256"] == load_parameters()[1]
        assert set(r.inputs["baselines"]) == set(sm.BASELINES) and r.model_version == "stat-v1.0"


def test_points_is_labelled_uncalibrated_and_the_others_are_not(db_session, artifact_dir):  # noqa: F811
    _world_with_stats(db_session)
    _snapshot(db_session)
    freeze_due(db_session, now=AT_T4H + timedelta(minutes=2))
    for r in db_session.query(NbaPropProjection).all():
        points = r.market == "player_points"
        assert r.distribution_kind == ("nb_bucket_uncalibrated" if points else "nb_bucket")
        assert r.inputs["validation"]["calibrated"] is (not points)


def test_v15_rows_are_unchanged(db_session, artifact_dir):  # noqa: F811
    _world_with_stats(db_session)
    _snapshot(db_session)
    before = _v15_state(db_session)
    freeze_due(db_session, now=AT_T4H + timedelta(minutes=2))
    assert _v15_state(db_session) == before


def test_rerun_is_a_no_op(db_session, artifact_dir):  # noqa: F811
    _world_with_stats(db_session)
    _snapshot(db_session)
    freeze_due(db_session, now=AT_T4H + timedelta(minutes=2))
    count = db_session.query(NbaPropProjection).count()
    again = freeze_due(db_session, now=AT_T4H + timedelta(minutes=10))
    assert again.already_frozen == 1 and not again.frozen and db_session.query(NbaPropProjection).count() == count


def test_a_closed_window_is_never_back_filled(db_session, artifact_dir):  # noqa: F811
    _world_with_stats(db_session)
    _snapshot(db_session)
    result = freeze_due(db_session, now=AT_T4H + T4H.tolerance + timedelta(seconds=1))
    assert len(result.window_closed) == 1 and not result.frozen and db_session.query(NbaPropProjection).count() == 0


def test_nothing_is_written_after_tip_off(db_session, artifact_dir):  # noqa: F811
    _world_with_stats(db_session)
    _snapshot(db_session, T1H, TIP - timedelta(hours=1))
    result = freeze_due(db_session, now=TIP + timedelta(minutes=1))
    assert not result.frozen and db_session.query(NbaPropProjection).count() == 0


def test_a_game_that_went_final_is_refused_inside_the_window(db_session, artifact_dir):  # noqa: F811
    *_, game, _ = _world_with_stats(db_session)
    _snapshot(db_session)
    game.status = "final"
    db_session.commit()
    result = freeze_due(db_session, now=AT_T4H + timedelta(minutes=2))
    assert len(result.failed) == 1 and db_session.query(NbaPropProjection).count() == 0


def test_only_the_configured_labels_are_used(db_session, artifact_dir):  # noqa: F811
    _world_with_stats(db_session)
    _snapshot(db_session, SnapshotSpec("T-30m", timedelta(minutes=30)), TIP - timedelta(minutes=30))
    assert "T-30m" not in FREEZE_LABELS
    result = freeze_due(db_session, now=TIP - timedelta(minutes=29))
    assert not result.frozen and not result.window_closed


def test_a_frozen_row_cannot_be_changed_or_deleted(db_session, artifact_dir):  # noqa: F811
    _world_with_stats(db_session)
    _snapshot(db_session)
    freeze_due(db_session, now=AT_T4H + timedelta(minutes=2))
    row = db_session.query(NbaPropProjection).first()
    row.predicted_mean = 99.0
    with pytest.raises(FrozenRecordError):
        db_session.flush()
    db_session.rollback()
    db_session.delete(db_session.query(NbaPropProjection).first())
    with pytest.raises(FrozenRecordError):
        db_session.flush()


def test_a_box_score_within_the_lag_or_recorded_later_is_not_read(db_session, artifact_dir):  # noqa: F811
    _, _, vet, *_ = _world_with_stats(db_session)
    _snapshot(db_session)
    now = AT_T4H + timedelta(minutes=2)
    base = dict(freeze_due(db_session, now=now, dry_run=True).frozen[0])
    baseline_rows = _frame_means(db_session, now)

    # A game that tipped off after the cutoff's lag: its box score must not count.
    recent = NbaGame(season_start_year=2026, game_date=(AT_T4H - timedelta(hours=2)).date(), scheduled_start=AT_T4H - timedelta(hours=2), status="final", home_team_id=db_session.query(NbaGame).first().home_team_id, away_team_id=db_session.query(NbaGame).first().away_team_id, home_score=1, away_score=2, source="test", source_game_id="lag")
    db_session.add(recent)
    db_session.flush()
    template = db_session.query(NbaPlayerGameLog).first()
    db_session.add(NbaPlayerGameLog(player_id=vet.id, game_id=recent.id, team_id=template.team_id, opponent_team_id=template.opponent_team_id, is_home=True, source="test", recorded_at=AT_T4H - timedelta(minutes=30), minutes=48.0, points=99, rebounds=99, assists=99, did_not_play=False))
    db_session.commit()
    assert _frame_means(db_session, now) == baseline_rows
    assert freeze_due(db_session, now=now, dry_run=True).frozen[0]["rows"] == base["rows"]


def _frame_means(db, now):
    """The means a freeze would write, from a dry computation."""
    from app.nba.minutes.data import load_games

    captured = {}
    original = sp.predict_rows

    def spy(rows, logs, games, parameters):
        frame = original(rows, logs, games, parameters)
        captured.update({int(r["player_id"]): round(float(r["points_stat_v1"]), 9) for _, r in frame.iterrows()})
        return frame

    sp.predict_rows = spy
    try:
        load_games(db)
        freeze_due(db, now=now, dry_run=True)
    finally:
        sp.predict_rows = original
    return captured


def test_dry_run_writes_nothing(db_session, artifact_dir):  # noqa: F811
    _world_with_stats(db_session)
    _snapshot(db_session)
    result = freeze_due(db_session, now=AT_T4H + timedelta(minutes=2), dry_run=True)
    assert result.frozen[0]["rows"] > 0 and db_session.query(NbaPropProjection).count() == 0


def test_one_failing_snapshot_does_not_stop_or_leave_partial_rows(db_session, artifact_dir, monkeypatch):  # noqa: F811
    _world_with_stats(db_session)
    _snapshot(db_session)

    def boom(*a, **k):
        raise RuntimeError("bad")

    monkeypatch.setattr(sp, "record_projection", boom)
    result = freeze_due(db_session, now=AT_T4H + timedelta(minutes=2))
    assert len(result.failed) == 1 and "bad" in result.failed[0]["error"] and db_session.query(NbaPropProjection).count() == 0
    monkeypatch.undo()
    retried = freeze_due(db_session, now=AT_T4H + timedelta(minutes=5))  # still inside the window
    assert len(retried.frozen) == 1


def test_evaluation_scores_the_frozen_rows_and_voids_dnps(db_session, artifact_dir):  # noqa: F811
    _, _, vet, _, game, _ = _world_with_stats(db_session)
    _snapshot(db_session)
    freeze_due(db_session, now=AT_T4H + timedelta(minutes=2))
    assert evaluate_frozen(db_session)["groups"]["points T-4h"]["pending_rows"] > 0

    frozen = {r.market: r.predicted_mean for r in db_session.query(NbaPropProjection).filter_by(player_id=vet.id)}
    game.status = "final"
    template = db_session.query(NbaPlayerGameLog).first()
    db_session.add(NbaPlayerGameLog(player_id=vet.id, game_id=game.id, team_id=template.team_id, opponent_team_id=template.opponent_team_id, is_home=True, source="test", recorded_at=TIP + timedelta(hours=3), minutes=30.0, points=20, rebounds=5, assists=4, did_not_play=False))
    db_session.commit()
    report = evaluate_frozen(db_session)
    points = report["groups"]["points T-4h"]
    assert points["scored_rows"] == 1 and points["validation"]["calibrated"] is False and points["distribution_kind"] == "nb_bucket_uncalibrated"
    assert points["means"]["stat_v1"]["mae"] == pytest.approx(abs(frozen["player_points"] - 20))
    assert report["integrity"] == {"generated_at_or_after_tipoff": 0, "generated_before_cutoff": 0}
    assert report["groups"]["rebounds T-4h"]["validation"]["calibrated"] is True

    log = db_session.query(NbaPlayerGameLog).filter_by(game_id=game.id).one()
    log.did_not_play, log.minutes, log.points, log.rebounds, log.assists = True, None, None, None, None
    db_session.commit()
    voided = evaluate_frozen(db_session)["groups"]["points T-4h"]
    assert voided["scored_rows"] == 0 and voided["void_rows"] == voided["frozen_rows"]


def test_evaluation_reports_snapshots_with_no_predictions(db_session, artifact_dir):  # noqa: F811
    _world_with_stats(db_session)
    _snapshot(db_session)
    freeze_due(db_session, now=AT_T4H + timedelta(minutes=2))
    _snapshot(db_session, T1H, TIP - timedelta(hours=1))  # this one is never frozen
    gap = db_session.query(NbaOutlookSnapshot).filter_by(snapshot_label="T-1h").one()
    coverage = evaluate_frozen(db_session)["coverage"]
    assert coverage["T-4h"] == {"snapshots_with_rows": 1, "frozen": 1, "missing_snapshot_ids": []}
    assert coverage["T-1h"] == {"snapshots_with_rows": 1, "frozen": 0, "missing_snapshot_ids": [gap.id]}


def test_distribution_prices_half_and_whole_lines():
    d = NbBucketDistribution(20.0, 0.1)
    assert d.prob_over(19.5) + d.prob_under(19.5) == pytest.approx(1.0)
    assert d.prob_over(20.0) + d.prob_under(20.0) < 1.0  # the remainder is the push


def _workflow(name):
    return (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_live_cycle_step_is_isolated_gated_and_after_the_snapshot():
    wf = _workflow("nba-live-cycle.yml")
    block = wf[wf.index("Freeze Stat Model V1 predictions"):]
    assert wf.index("Freeze due V1.5 outlook snapshots") < wf.index("Freeze Stat Model V1 predictions")
    assert "vars.NBA_STAT_PREDICTIONS_ENABLED == 'true'" in block and "continue-on-error: true" in block and "timeout-minutes: 5" in block
    assert "vars.NBA_STAT_PREDICTIONS_IMAGE" in block and "python -m app.nba.stats.cli freeze" in block
    assert "THE_ODDS_API_KEY" not in wf


def test_manual_workflow_is_read_only_and_never_fetches_odds():
    wf = _workflow("nba-stat-predictions.yml")
    assert "default_transaction_read_only=on" in wf and "workflow_dispatch" in wf and "schedule:" not in wf
    assert "ODDS" not in wf and "cli freeze --dry-run" in wf and "cli evaluate" in wf
