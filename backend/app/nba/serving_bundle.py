"""The canonical V1.5 serving bundle: the exact fitted models that produced
the published Minutes V1 and rotation-layer results, shipped inside the
application image.

Why this exists: the experiments save fitted models under
backend/model_artifacts/ (git-ignored) and record them in the LOCAL
database's nba_minutes_model_runs with absolute local paths. Neither the
files nor the registry rows exist in the production image or the hosted
database, and production must never retrain a different model.

app/nba/serving_models/ holds:
- the three fitted model files (V1 minutes, P(play), P(10+ minutes)),
  byte-identical to the registry artifacts (SHA-256 checked);
- manifest.json: the registry metadata of the four canonical serving runs
  (the three models plus the reconciliation config, which is not adopted),
  exported from the local registry, plus GOLDEN predictions - fixed inputs
  and the outputs the models must reproduce exactly.

`verify_bundle()` proves, wherever it runs (CI, inside the built image),
that the files are present, unmodified, loadable, and predict exactly as
they did when exported. `import_bundle(db)` writes the four runs into a
database's registry (append-only; idempotent by run_key) with artifact paths
pointing into the bundle. Nothing here fits anything.
"""

import hashlib
import json
import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prospective import ensure_utc
from app.models.nba import NbaMinutesModelRun

BUNDLE_DIR = Path(__file__).resolve().parent / "serving_models"
MANIFEST_PATH = BUNDLE_DIR / "manifest.json"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_TOLERANCE = 1e-9


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def bundle_relative_path(artifact_file: str) -> str:
    """Artifact path as stored in the registry: relative to the backend root,
    so it resolves inside the image (/app/...) and in a local checkout alike."""
    return (BUNDLE_DIR / artifact_file).relative_to(BACKEND_ROOT).as_posix()


def _load(entry: dict):
    blob = (BUNDLE_DIR / entry["artifact_file"]).read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != entry["artifact_sha256"]:
        raise ValueError(f"bundle file {entry['artifact_file']} has SHA-256 {digest}, manifest says {entry['artifact_sha256']}")
    return pickle.loads(blob)  # noqa: S301 - our own file, hash-verified above


def _predict(entry: dict, model, X: np.ndarray) -> np.ndarray:
    if entry["key"] == "minutes":
        return np.asarray(model.predict(X), dtype=float)
    return np.asarray(model.predict_proba(X)[:, 1], dtype=float)


def golden_inputs(features: list[str], n: int = 6) -> np.ndarray:
    """Fixed, deterministic inputs spanning plausible values (and missing
    values) for every feature."""
    rng = np.random.default_rng(20261004)
    X = rng.uniform(0, 1, (n, len(features)))
    scale = {"min": 40.0, "season": 40.0, "prev": 40.0, "team_min": 40.0, "career": 82.0, "games": 82.0, "days": 30.0, "rest": 7.0, "rank": 13.0}
    for j, f in enumerate(features):
        for prefix, s in scale.items():
            if f.startswith(prefix):
                X[:, j] *= s
                break
    X[0, : len(features) // 3] = np.nan  # a row with many missing values
    return X


@dataclass
class VerifyResult:
    ok: bool
    lines: list[str]


def verify_bundle() -> VerifyResult:
    manifest = load_manifest()
    lines, ok = [f"bundle {manifest['bundle_version']} at {BUNDLE_DIR}"], True
    for entry in manifest["models"]:
        if not entry.get("artifact_file"):
            lines.append(f"  {entry['key']}: configuration only (no model file); adopted={entry['hyperparameters'].get('adopted')}")
            continue
        try:
            model = _load(entry)
            X = golden_inputs(entry["features"])
            got = _predict(entry, model, X)
            want = np.asarray(entry["golden_outputs"], dtype=float)
            diff = float(np.max(np.abs(got - want)))
            if diff > GOLDEN_TOLERANCE:
                ok = False
                lines.append(f"  {entry['key']}: GOLDEN MISMATCH max |diff| {diff}")
            else:
                lines.append(f"  {entry['key']}: ok - sha256 {entry['artifact_sha256'][:12]}, golden predictions reproduced (max |diff| {diff:.1e})")
        except Exception as exc:  # report every problem, then fail
            ok = False
            lines.append(f"  {entry['key']}: FAILED {type(exc).__name__}: {exc}")
    return VerifyResult(ok, lines)


def import_bundle(db: Session) -> list[str]:
    """Write the canonical serving runs into this database's registry.
    Idempotent: a run_key already present is left untouched (and must match)."""
    verified = verify_bundle()
    if not verified.ok:
        raise ValueError("bundle verification failed:\n" + "\n".join(verified.lines))
    out = []
    for entry in load_manifest()["models"]:
        existing = db.scalar(select(NbaMinutesModelRun).where(NbaMinutesModelRun.run_key == entry["run_key"]))
        if existing is not None:
            if existing.artifact_sha256 != entry["artifact_sha256"]:
                raise ValueError(f"run {entry['run_key']} exists with a different artifact")
            out.append(f"{entry['key']}: already present as run {existing.id}")
            continue
        run = NbaMinutesModelRun(
            run_key=entry["run_key"], model_name=entry["model_name"], model_version=entry["model_version"], purpose=entry["purpose"],
            run_at=ensure_utc(datetime.fromisoformat(str(entry["run_at"]))), code_version=entry["code_version"],
            dataset_cutoff=ensure_utc(datetime.fromisoformat(str(entry["dataset_cutoff"]))),
            training_seasons=entry["training_seasons"], evaluation_seasons=entry["evaluation_seasons"], features=entry["features"],
            hyperparameters=entry["hyperparameters"], metrics=entry["metrics"], uncertainty=entry["uncertainty"],
            artifact_path=bundle_relative_path(entry["artifact_file"]) if entry.get("artifact_file") else None,
            artifact_sha256=entry["artifact_sha256"],
            notes=(entry.get("notes") or "") + f" [imported from serving bundle; local run {entry['source_local_run_id']}]",
        )
        db.add(run)
        db.flush()
        out.append(f"{entry['key']}: imported as run {run.id}")
    db.commit()
    return out
