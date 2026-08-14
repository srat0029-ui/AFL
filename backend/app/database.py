"""SQLAlchemy engine/session setup.

Uses SQLite for local development (zero setup). Swapping to Postgres later is a
one-line change: set DATABASE_URL to a postgresql+psycopg2://... URL and install
psycopg2-binary — no application code changes needed since everything here reads
the URL from settings and SQLAlchemy handles dialect differences.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=_connect_args)

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
