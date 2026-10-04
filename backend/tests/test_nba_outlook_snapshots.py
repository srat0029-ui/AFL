"""Frozen V1.5 outlook snapshots (T-24h / T-4h / T-1h / T-30m) against a real
database session (also run against PostgreSQL in CI)."""

from datetime import timedelta

import pytest

from app.core.prospective import FrozenRecordError, ensure_utc
from app.models.nba import NbaOutlookSnapshot, NbaPlayer, NbaRotationPrediction
from app.models.nba.outlook import SNAPSHOT_MISSED_WINDOW, SNAPSHOT_MISSING_DATA, SNAPSHOT_PARTIAL, SNAPSHOT_PRODUCED
from app.nba.outlooks.snapshots import DEFAULT_SNAPSHOTS, SnapshotSpec, parse_specs, run_due_snapshots
from tests.test_nba_minutes_prospective import NOW, _roster, _serving_run, _world, artifact_dir  # noqa: F401  (fixture)
from tests.test_nba_rotation_prospective import _report, _rotation_runs

TIP = NOW + timedelta(hours=10)  # the upcoming game in _world


def _setup(db, *, rosters_at=None, home_ids=("1001", "1002"), away_ids=()):
    home, away, vet, rookie, game = _world(db)
    _serving_run(db)
    runs = _rotation_runs(db, adopted=False)  # as served: reconciliation not adopted
    if rosters_at is not None:
        _roster(db, home, rosters_at, list(home_ids))
        _roster(db, away, rosters_at, list(away_ids))
    return home, away, vet, rookie, game, runs


def _snap(db, label):
    return db.query(NbaOutlookSnapshot).filter_by(snapshot_label=label).one()


def _rows(db, snap):
    return db.query(NbaRotationPrediction).filter_by(snapshot_id=snap.id).all()


def test_default_labels_and_parsing():
    assert [s.label for s in DEFAULT_SNAPSHOTS] == ["T-24h", "T-4h", "T-1h", "T-30m"]
    assert parse_specs("T-24h, T-90m") == (SnapshotSpec("T-24h", timedelta(hours=24)), SnapshotSpec("T-90m", timedelta(minutes=90)))
    with pytest.raises(ValueError):
        parse_specs("24h")


def test_nothing_happens_before_a_target_cutoff(db_session, artifact_dir):  # noqa: F811
    _setup(db_session, rosters_at=TIP - timedelta(hours=30))
    r = run_due_snapshots(db_session, now=TIP - timedelta(hours=24, minutes=1))
    assert r.produced == [] and r.recorded_without_rows == [] and db_session.query(NbaOutlookSnapshot).count() == 0


@pytest.mark.parametrize("label,offset", [("T-24h", timedelta(hours=24)), ("T-4h", timedelta(hours=4)), ("T-1h", timedelta(hours=1)), ("T-30m", timedelta(minutes=30))])
def test_each_label_freezes_at_its_own_cutoff(db_session, artifact_dir, label, offset):  # noqa: F811
    _setup(db_session, rosters_at=TIP - timedelta(hours=30))
    now = TIP - offset + timedelta(minutes=5)
    run_due_snapshots(db_session, now=now, specs=(SnapshotSpec(label, offset),))
    snap = _snap(db_session, label)
    assert snap.status == SNAPSHOT_PRODUCED
    assert ensure_utc(snap.target_cutoff) == TIP - offset
    assert ensure_utc(snap.information_cutoff) == now and ensure_utc(snap.generated_at) == now
    assert ensure_utc(snap.box_score_cutoff) == now - timedelta(hours=4)
    for row in _rows(db_session, snap):
        assert ensure_utc(row.information_cutoff) == now


def test_a_late_runner_records_a_missed_window_instead_of_a_mislabelled_snapshot(db_session, artifact_dir):  # noqa: F811
    _setup(db_session, rosters_at=TIP - timedelta(hours=30))
    r = run_due_snapshots(db_session, now=TIP - timedelta(hours=4))  # first call ever, at T-4h
    by = {s.snapshot_label: s for s in db_session.query(NbaOutlookSnapshot).all()}
    assert by["T-24h"].status == SNAPSHOT_MISSED_WINDOW and _rows(db_session, by["T-24h"]) == []
    assert by["T-4h"].status == SNAPSHOT_PRODUCED and _rows(db_session, by["T-4h"])
    assert "T-1h" not in by and "T-30m" not in by
    assert len(r.produced) == 1


def test_frozen_snapshot_is_never_redone_or_edited(db_session, artifact_dir):  # noqa: F811
    _setup(db_session, rosters_at=TIP - timedelta(hours=30))
    run_due_snapshots(db_session, now=TIP - timedelta(hours=4), specs=(SnapshotSpec("T-4h", timedelta(hours=4)),))
    again = run_due_snapshots(db_session, now=TIP - timedelta(hours=3, minutes=50), specs=(SnapshotSpec("T-4h", timedelta(hours=4)),))
    assert again.skipped_existing == 1 and db_session.query(NbaOutlookSnapshot).count() == 1
    snap = _snap(db_session, "T-4h")
    snap.status = SNAPSHOT_MISSING_DATA
    with pytest.raises(FrozenRecordError):
        db_session.flush()
    db_session.rollback()
    row = _rows(db_session, _snap(db_session, "T-4h"))[0]
    row.p_play = 1.0
    with pytest.raises(FrozenRecordError):
        db_session.flush()


def test_each_snapshot_sees_only_evidence_known_at_its_cutoff(db_session, artifact_dir):  # noqa: F811
    home, _, vet, _, _, _ = _setup(db_session, rosters_at=TIP - timedelta(hours=30))
    _report(db_session, vet, home, TIP - timedelta(hours=2), "out")  # reported between T-4h and T-1h
    run_due_snapshots(db_session, now=TIP - timedelta(hours=4), specs=(SnapshotSpec("T-4h", timedelta(hours=4)),))
    run_due_snapshots(db_session, now=TIP - timedelta(hours=1), specs=(SnapshotSpec("T-1h", timedelta(hours=1)),))
    early = next(r for r in _rows(db_session, _snap(db_session, "T-4h")) if r.player_id == vet.id)
    late = next(r for r in _rows(db_session, _snap(db_session, "T-1h")) if r.player_id == vet.id)
    assert early.availability_evidence["state"] != "listed" and early.experimental_rules == {}
    assert late.availability_evidence["state"] == "listed" and late.availability_evidence["status"] == "out"
    assert late.experimental_rules["experimental_out_status_v0"]["applied_to_learned_fields"] is False
    # Canonical V1.5 outputs are untouched by the injury evidence.
    for f in ("p_play", "p_rotation", "expected_minutes_if_plays", "tier"):
        assert getattr(early, f) == getattr(late, f)
    assert _snap(db_session, "T-1h").availability_feed_last_read_at is not None
    assert _snap(db_session, "T-4h").availability_feed_last_read_at is None  # feed not read by then


def test_current_roster_defines_candidates_departed_and_traded_players(db_session, artifact_dir):  # noqa: F811
    # Veteran (last box score for HOME) now appears on AWAY's observed roster: a trade.
    home, away, vet, rookie, _, _ = _setup(db_session, rosters_at=TIP - timedelta(hours=30), home_ids=("1002",), away_ids=("1001",))
    run_due_snapshots(db_session, now=TIP - timedelta(hours=4), specs=(SnapshotSpec("T-4h", timedelta(hours=4)),))
    rows = {(r.player_id, r.team_id): r for r in _rows(db_session, _snap(db_session, "T-4h"))}
    assert (vet.id, home.id) not in rows  # departed from home: not a home candidate
    traded = rows[(vet.id, away.id)]
    assert traded.inputs["traded"] == 1.0 and traded.inputs["games_with_team"] == 0.0 and traded.roster_listed
    new = rows[(rookie.id, home.id)]
    assert new.expected_minutes_if_plays is None and new.quantiles is None and 0 <= new.p_play <= 1  # no invented minutes


def test_roster_observed_after_cutoff_is_not_used(db_session, artifact_dir):  # noqa: F811
    _setup(db_session, rosters_at=TIP - timedelta(hours=3))  # observed after the T-4h cutoff
    run_due_snapshots(db_session, now=TIP - timedelta(hours=4), specs=(SnapshotSpec("T-4h", timedelta(hours=4)),))
    snap = _snap(db_session, "T-4h")
    assert snap.status == SNAPSHOT_MISSING_DATA and "no roster observation" in snap.reason
    assert _rows(db_session, snap) == [] and all(v is None for v in snap.roster_evidence.values())


def test_partial_snapshot_when_one_team_has_no_roster_evidence(db_session, artifact_dir):  # noqa: F811
    home, away, *_ = _world(db_session)
    _serving_run(db_session)
    _rotation_runs(db_session)
    _roster(db_session, home, TIP - timedelta(hours=30), ["1001"])
    run_due_snapshots(db_session, now=TIP - timedelta(hours=4), specs=(SnapshotSpec("T-4h", timedelta(hours=4)),))
    snap = _snap(db_session, "T-4h")
    assert snap.status == SNAPSHOT_PARTIAL and str(away.id) in snap.reason
    assert snap.roster_evidence[str(home.id)]["players"] == 1 and snap.roster_evidence[str(away.id)] is None
    assert {r.team_id for r in _rows(db_session, snap)} == {home.id}


def test_missing_models_are_recorded_not_faked(db_session, artifact_dir):  # noqa: F811
    home, away, *_ = _world(db_session)
    _roster(db_session, home, TIP - timedelta(hours=30), ["1001"])
    run_due_snapshots(db_session, now=TIP - timedelta(hours=1), specs=(SnapshotSpec("T-1h", timedelta(hours=1)),))
    snap = _snap(db_session, "T-1h")
    assert snap.status == SNAPSHOT_MISSING_DATA and "serving runs" in snap.reason and _rows(db_session, snap) == []


def test_snapshot_records_model_versions(db_session, artifact_dir):  # noqa: F811
    _, _, _, _, _, runs = _setup(db_session, rosters_at=TIP - timedelta(hours=30))
    run_due_snapshots(db_session, now=TIP - timedelta(minutes=30), specs=(SnapshotSpec("T-30m", timedelta(minutes=30)),))
    mv = _snap(db_session, "T-30m").model_versions
    assert mv["participation"]["run_id"] == runs["participation"].id and mv["participation"]["model_version"] == "rotation-v1.0"
    assert mv["minutes"]["model_version"].startswith("minutes-v1") and mv["minutes"]["artifact_sha256"]
    row = _rows(db_session, _snap(db_session, "T-30m"))[0]
    assert row.participation_model_run_id == runs["participation"].id and row.reconciled_minutes_if_plays is None


def test_unknown_roster_player_is_counted(db_session, artifact_dir):  # noqa: F811
    _setup(db_session, rosters_at=TIP - timedelta(hours=30), home_ids=("1001", "9999"))
    run_due_snapshots(db_session, now=TIP - timedelta(hours=4), specs=(SnapshotSpec("T-4h", timedelta(hours=4)),))
    snap = _snap(db_session, "T-4h")
    assert snap.counts["unknown_player"] == 1 and db_session.query(NbaPlayer).filter_by(source_player_id="9999").count() == 0


def test_dry_run_writes_nothing(db_session, artifact_dir):  # noqa: F811
    _setup(db_session, rosters_at=TIP - timedelta(hours=30))
    r = run_due_snapshots(db_session, now=TIP - timedelta(hours=4), specs=(SnapshotSpec("T-4h", timedelta(hours=4)),), dry_run=True)
    assert r.produced and r.produced[0]["rows_preview"] and db_session.query(NbaOutlookSnapshot).count() == 0
