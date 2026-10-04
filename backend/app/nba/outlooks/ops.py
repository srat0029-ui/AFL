"""Read-only operational measurements for the hosted outlook pipeline.

- database_size(): pg_database_size and the largest tables (PostgreSQL only).
- measured(): wraps a run with wall-clock time and peak memory.
Nothing here writes to the database.
"""

import sys
import time
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session


def database_size(db: Session, *, top: int = 20) -> list[str]:
    if db.get_bind().dialect.name != "postgresql":
        return ["database size is only measured on PostgreSQL"]
    total = db.execute(text("select pg_database_size(current_database())")).scalar_one()
    rows = db.execute(
        text(
            "select relname, pg_total_relation_size(relid) as bytes, n_live_tup "
            "from pg_stat_user_tables order by pg_total_relation_size(relid) desc limit :n"
        ),
        {"n": top},
    ).all()
    lines = [f"pg_database_size: {total} bytes = {total / 1024 / 1024:.1f} MB"]
    lines.append(f"headroom against a 5 GB (5120 MB) plan: {5120 - total / 1024 / 1024:.1f} MB")
    lines.append(f"largest {top} tables (total relation size incl. indexes; approximate live rows):")
    for name, size, live in rows:
        lines.append(f"  {name:<45} {size / 1024 / 1024:9.2f} MB  ~{live} rows")
    return lines


def peak_memory_mb() -> float | None:
    try:
        import resource  # POSIX only (the production image is Linux)
    except ImportError:
        return None
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1024 / 1024 if sys.platform == "darwin" else rss / 1024  # bytes on macOS, KiB on Linux


@contextmanager
def measured(label: str, out=print):
    start = time.perf_counter()
    try:
        yield
    finally:
        mem = peak_memory_mb()
        out(f"[measure] {label}: wall clock {time.perf_counter() - start:.1f} s; peak memory {'n/a' if mem is None else f'{mem:.0f} MB'}")
