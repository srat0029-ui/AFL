"""Making V1.5 outlooks runnable in the hosted environment: the historical
backfill's safety, season coverage checks, the canonical serving bundle, and
the workflows that run them."""

import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.models.nba import NbaGame, NbaMinutesModelRun, NbaOutlookSnapshot, NbaPlayer, NbaPlayerGameLog, NbaTeam
from app.nba import serving_bundle
from app.nba.ingestion import sync_box_scores
from app.nba.minutes.prospective import latest_serving_run
from app.nba.outlooks.snapshots import SnapshotSpec, run_due_snapshots
from app.nba.rotation.prospective import load_serving
from app.nba.validation import season_coverage
from app.providers.nba.espn import PROVIDER_NAME
from tests.test_nba_ingestion import _synced
from tests.test_nba_minutes_prospective import _roster, _world
from tests.test_nba_outlook_snapshots import SCHEDULE_OBSERVED, TIP, _schedule_obs

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"


# --- backfill must not rewrite current player information ----------------------


def _player_with_current_info(db, team_abbr: str) -> NbaPlayer:
    team = db.query(NbaTeam).filter_by(abbreviation=team_abbr).one()
    player = NbaPlayer(display_name="Name From Live Feed", source=PROVIDER_NAME, source_player_id="4277961", current_team_id=team.id)
    db.add(player)
    db.commit()
    return player


def test_hosted_backfill_keeps_players_current_team_and_name(db_session):
    provider = _synced(db_session)
    player = _player_with_current_info(db_session, "ORL")  # e.g. traded since the historical game
    report = sync_box_scores(db_session, provider, date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, limit=1, follow_latest=False)
    assert report.logs_created == 26 and report.players_created == 25  # the other players are still created
    db_session.refresh(player)
    assert player.current_team.abbreviation == "ORL" and player.display_name == "Name From Live Feed" and player.last_game_at is None
    log = db_session.query(NbaPlayerGameLog).filter_by(player_id=player.id).one()
    assert log.team.abbreviation == "MEM"  # the historical fact itself is stored as published


def test_default_ingestion_still_follows_the_latest_game(db_session):
    provider = _synced(db_session)
    player = _player_with_current_info(db_session, "ORL")
    sync_box_scores(db_session, provider, date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, limit=1)
    db_session.refresh(player)
    assert player.current_team.abbreviation == "MEM"


def test_season_coverage_counts(db_session):
    provider = _synced(db_session)
    before = season_coverage(db_session, 2025)
    assert before["competitive_final_games"] >= 1 and before["stored_box_scores"] == 0 and before["box_score_coverage"] == 0.0
    sync_box_scores(db_session, provider, date(2026, 1, 15), date(2026, 1, 15), source=PROVIDER_NAME, limit=1)
    after = season_coverage(db_session, 2025)
    assert after["stored_box_scores"] == 1 and after["player_logs"] == 26 and after["players"] == 26
    assert after["box_score_coverage"] == pytest.approx(1 / after["competitive_final_games"])


# --- the canonical serving bundle ------------------------------------------------


def test_bundle_is_complete_unmodified_and_reproduces_golden_predictions():
    result = serving_bundle.verify_bundle()
    assert result.ok, "\n".join(result.lines)
    keys = {e["key"]: e for e in serving_bundle.load_manifest()["models"]}
    assert set(keys) == {"minutes", "participation", "rotation_10", "reconciliation"}
    assert keys["minutes"]["model_version"] == "minutes-v1.0" and keys["participation"]["model_version"] == "rotation-v1.0"
    assert keys["reconciliation"]["hyperparameters"]["adopted"] is False and keys["reconciliation"]["artifact_file"] is None
    assert keys["minutes"]["artifact_sha256"].startswith("cef5829d526f")
    assert keys["participation"]["artifact_sha256"].startswith("f5a0fc5f54f8")
    assert keys["rotation_10"]["artifact_sha256"].startswith("49afbc289651")


def test_a_tampered_bundle_file_is_detected(tmp_path, monkeypatch):
    copy = tmp_path / "serving_models"
    shutil.copytree(serving_bundle.BUNDLE_DIR, copy)
    victim = next(copy.glob("rotation-v1.0-participation-*.pkl"))
    victim.write_bytes(victim.read_bytes() + b"x")
    monkeypatch.setattr(serving_bundle, "BUNDLE_DIR", copy)
    monkeypatch.setattr(serving_bundle, "MANIFEST_PATH", copy / "manifest.json")
    result = serving_bundle.verify_bundle()
    assert not result.ok and any("participation" in line and "FAILED" in line for line in result.lines)


def test_bundle_import_is_idempotent_and_never_trains(db_session):
    first = serving_bundle.import_bundle(db_session)
    assert all("imported as run" in line for line in first)
    second = serving_bundle.import_bundle(db_session)
    assert all("already present" in line for line in second)
    runs = db_session.query(NbaMinutesModelRun).all()
    assert len(runs) == 4 and all(r.purpose == "serving" for r in runs)
    files = [r.artifact_path for r in runs if r.artifact_path]
    assert len(files) == 3 and all(p.startswith("app/nba/serving_models/") for p in files)  # relative: resolves inside the image


def test_imported_bundle_serves_real_frozen_outlooks(db_session):
    serving_bundle.import_bundle(db_session)
    assert latest_serving_run(db_session).model_version == "minutes-v1.0"
    serving = load_serving(db_session)  # loads and hash-checks the bundled files
    assert serving.recon_run.hyperparameters["adopted"] is False
    home, away, vet, _, game = _world(db_session)
    _schedule_obs(db_session, game, SCHEDULE_OBSERVED)
    _roster(db_session, home, TIP - timedelta(hours=30), ["1001"])
    _roster(db_session, away, TIP - timedelta(hours=30), [])
    run_due_snapshots(db_session, now=TIP - timedelta(hours=4), specs=(SnapshotSpec("T-4h", timedelta(hours=4)),))
    snap = db_session.query(NbaOutlookSnapshot).one()
    assert snap.model_versions["minutes"]["artifact_sha256"].startswith("cef5829d526f")
    assert snap.status in ("produced", "stale")
    assert db_session.query(NbaGame).filter_by(id=game.id).one() is not None


# --- workflows ------------------------------------------------------------------


def test_hosted_backfill_workflow_safeguards():
    wf = (WORKFLOWS / "nba-hosted-backfill.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in wf and "schedule:" not in wf  # manual only
    assert 'options: ["2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025"]' in wf
    assert '"$CONFIRM" != "backfill-$SEASON"' in wf
    assert "python -m app.nba.cli backfill --from-season \"$SEASON\" --to-season \"$SEASON\" --keep-player-display" in wf
    assert "season-coverage" in wf and "secrets.DATABASE_URL" in wf and "vars.NBA_LIVE_CYCLE_IMAGE" in wf
    assert "group: nba-hosted-backfill" in wf and "group: nba-live-cycle" not in wf  # never blocks the live cycle
    for forbidden in ("alembic", "pg_restore", "psql", "DROP ", "TRUNCATE", "app.ingestion", "app.cli", "live-cycle"):
        assert forbidden not in wf, forbidden


def test_live_cycle_runs_snapshots_only_when_enabled_and_after_evidence():
    wf = (WORKFLOWS / "nba-live-cycle.yml").read_text(encoding="utf-8")
    assert "vars.NBA_OUTLOOK_SNAPSHOTS_ENABLED == 'true'" in wf
    assert wf.index("Run the NBA command from the pinned image") < wf.index("Freeze due V1.5 outlook snapshots")
    assert "python -m app.nba.outlooks.cli snapshot" in wf
    assert 'cron: "14,29,44,59 * * * *"' in wf


def test_ci_verifies_the_bundle_inside_the_built_image():
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "docker run --rm afl-backend:ci python -m app.nba.outlooks.cli verify-serving-models" in ci


def test_cloudflare_primary_cron_moved_off_04():
    toml = (REPO / "cloudflare" / "nba-live-cycle-trigger" / "wrangler.toml").read_text(encoding="utf-8")
    assert 'crons = ["9,24,39,54 * * * *"]' in toml


def test_outlook_admin_workflow_is_manual_and_limited():
    wf = (WORKFLOWS / "nba-outlook-admin.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in wf and "schedule:" not in wf
    assert "options: [verify-serving-models, import-serving-models, snapshot-dry-run, registry, db-size]" in wf
    assert "snapshot --dry-run" in wf and "alembic" not in wf and "backfill" not in wf


def test_custom_snapshot_labels_are_refused_outside_dry_run():
    from app.nba.outlooks.cli import main

    with pytest.raises(SystemExit):
        main(["snapshot", "--labels", "T-372h"])


def test_db_size_is_read_only_and_postgres_only(db_session):
    from app.nba.outlooks.ops import database_size

    lines = database_size(db_session)
    if db_session.get_bind().dialect.name == "postgresql":
        assert lines[0].startswith("pg_database_size:") and "headroom" in lines[1]
    else:
        assert lines == ["database size is only measured on PostgreSQL"]


def test_db_size_workflow_is_manual_and_read_only():
    wf = (WORKFLOWS / "nba-db-size.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in wf and "schedule:" not in wf
    assert "default_transaction_read_only=on" in wf and "pg_database_size" in wf
    for forbidden in ("INSERT", "UPDATE ", "DELETE", "DROP", "TRUNCATE", "ALTER", "pg_restore", "alembic"):
        assert forbidden not in wf.upper(), forbidden


def test_admin_image_override_is_limited_to_verification():
    wf = (WORKFLOWS / "nba-outlook-admin.yml").read_text(encoding="utf-8")
    assert "image_override is allowed only with verify-serving-models" in wf
    verify_block = wf[wf.index("verify-serving-models)"): wf.index("import-serving-models)")]
    assert "DATABASE_URL" not in verify_block  # verification never touches the database
