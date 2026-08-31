"""Deployment provenance: which exact code is running.

Resolution order for git_sha/build_time:
1. A `release_info.json` baked into the image at Docker build time (see
   backend/Dockerfile's GIT_SHA/BUILD_TIME build args) - the real answer in
   any deployed environment, since `.git` is never copied into the image
   (see .dockerignore).
2. `git rev-parse HEAD` via subprocess - works in local dev, where `.git`
   exists but no baked file does.
3. "unknown" - never crashes the app just because provenance can't be
   determined.

Cached at first call (this only ever changes when the process restarts on
a new deploy/checkout).
"""

import json
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_RELEASE_INFO_PATH = Path(__file__).resolve().parent.parent / "release_info.json"


@dataclass(frozen=True)
class ReleaseInfo:
    git_sha: str
    build_time: str


def _from_baked_file() -> ReleaseInfo | None:
    if not _RELEASE_INFO_PATH.exists():
        return None
    try:
        data = json.loads(_RELEASE_INFO_PATH.read_text(encoding="utf-8"))
        return ReleaseInfo(git_sha=data.get("git_sha", "unknown"), build_time=data.get("build_time", "unknown"))
    except (OSError, json.JSONDecodeError):
        return None


def _from_git() -> ReleaseInfo | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return ReleaseInfo(git_sha=result.stdout.strip()[:12], build_time="unknown (local dev - not a built image)")


@lru_cache
def get_release_info() -> ReleaseInfo:
    return _from_baked_file() or _from_git() or ReleaseInfo(git_sha="unknown", build_time="unknown")
