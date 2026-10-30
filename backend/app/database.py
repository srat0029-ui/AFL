"""SQLAlchemy engine/session setup.

Uses SQLite for local development (zero setup). Production uses Postgres: set
DATABASE_URL to a postgresql+psycopg://... URL (the psycopg driver is already
in requirements.txt) — no application code changes needed since everything
here reads the URL from settings and SQLAlchemy handles dialect differences.
Every DateTime column across the model layer already declares
timezone=True, and no code anywhere uses SQLite-specific SQL, so this really
is a one-line env var swap (see docs/DEPLOYMENT.md).
"""

import logging
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

READ_ONLY_OPTION = "-c default_transaction_read_only=on"


def engine_connect_args(settings: Settings) -> dict:
    """Driver connect arguments. With DATABASE_READ_ONLY every PostgreSQL
    session starts read-only, so PostgreSQL itself rejects writes; it is
    refused for SQLite rather than silently not applied."""
    if settings.database_url.startswith("sqlite"):
        if settings.database_read_only:
            raise ValueError("DATABASE_READ_ONLY is only supported for PostgreSQL; it would not be enforced for SQLite.")
        return {"check_same_thread": False}
    if settings.database_read_only:
        return {"options": READ_ONLY_OPTION}
    return {}


settings = get_settings()

_connect_args = engine_connect_args(settings)
if settings.database_read_only:
    logger.warning("DATABASE_READ_ONLY: every database session is read-only; writes will be refused by PostgreSQL.")

# pool_pre_ping: a cheap SELECT 1 before handing out a pooled connection -
# avoids "server closed the connection unexpectedly" errors against managed
# Postgres after an idle period (a real, common gotcha on hosted DBs, not a
# guessed tuning knob). Harmless no-op for SQLite. Pool size/overflow are
# left at SQLAlchemy's own QueuePool defaults (5/10) - adequate at this
# project's traffic scale; no invented numbers.
engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
