"""Application-wide logging configuration.

Deliberately not structlog/python-json-logger — a single consistently
parseable `key=value` line format needs no new dependency and is enough to
grep/aggregate on `request_id`/`consumer_id` in any log viewer. Called once
from app/main.py before routers are registered, so every logger in the
process (including uvicorn's own) inherits one root formatter/handler.
"""

import logging

from app.release_info import get_release_info

_FORMAT = "%(asctime)s level=%(levelname)s logger=%(name)s release=%(release_sha)s %(message)s"


class _ReleaseFilter(logging.Filter):
    """Injects the release git SHA into every log record so log lines can
    be tied back to the exact code version that produced them (item 8's
    request/version/model-version/data-cutoff chain)."""

    def __init__(self, release_sha: str) -> None:
        super().__init__()
        self._release_sha = release_sha

    def filter(self, record: logging.LogRecord) -> bool:
        record.release_sha = self._release_sha
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(_ReleaseFilter(get_release_info().git_sha))
    root.addHandler(handler)
