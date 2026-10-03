"""Append-only records of minutes-model runs, and the fitted-model files.

A run row is never updated: a re-run is a new row with a new run_key. A
fitted model is saved under MODEL_ARTIFACT_DIR with its SHA-256 recorded on
the run, and loading verifies the hash, so a prediction can always be traced
to the exact model file that made it.
"""

import hashlib
import pickle
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.nba import NbaMinutesModelRun

MODEL_ARTIFACT_DIR = Path(__file__).resolve().parents[3] / "model_artifacts" / "nba_minutes"


def code_version() -> str | None:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=Path(__file__).parent).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True, cwd=Path(__file__).parent).stdout.strip()
        return f"{sha}{'+dirty' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return None


def save_model(model, name: str) -> tuple[str, str]:
    MODEL_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_ARTIFACT_DIR / f"{name}.pkl"
    if path.exists():
        raise FileExistsError(f"{path} already exists - model files are never overwritten")
    blob = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
    path.write_bytes(blob)
    return str(path), hashlib.sha256(blob).hexdigest()


def load_model(run: NbaMinutesModelRun):
    if not run.artifact_path:
        raise ValueError(f"run {run.run_key} has no model file")
    blob = Path(run.artifact_path).read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != run.artifact_sha256:
        raise ValueError(f"model file {run.artifact_path} does not match the recorded SHA-256 for run {run.run_key}")
    return pickle.loads(blob)  # noqa: S301 - our own file, hash-verified above


def record_run(db: Session, **fields) -> NbaMinutesModelRun:
    run_at = fields.pop("run_at", None) or datetime.now(timezone.utc)
    run = NbaMinutesModelRun(run_at=run_at, code_version=fields.pop("code_version", code_version()), **fields)
    db.add(run)
    db.flush()
    return run
