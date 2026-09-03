"""Tests for app/config.py's database URL normalization - a provider-issued
bare `postgresql://` connection string must resolve to the Psycopg 3 DBAPI
this project actually has installed (see requirements.txt), not the
unavailable psycopg2 SQLAlchemy defaults to for that scheme.
"""

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.config import Settings, normalize_database_url


def test_bare_postgresql_url_selects_psycopg3():
    normalized = normalize_database_url("postgresql://user:pw@host:5432/db")
    assert normalized == "postgresql+psycopg://user:pw@host:5432/db"
    assert make_url(normalized).get_driver_name() == "psycopg"


def test_already_explicit_psycopg_url_is_unchanged():
    url = "postgresql+psycopg://user:pw@host:5432/db"
    assert normalize_database_url(url) == url


def test_sqlite_url_is_unchanged():
    url = "sqlite:///./afl.db"
    assert normalize_database_url(url) == url


def test_query_parameters_and_custom_port_survive_unchanged():
    # Shaped like a real managed-Postgres connection string (custom port,
    # sslmode) without using any real hostname.
    url = "postgresql://postgres:secret@example-db.invalid:33051/afl_production?sslmode=require"
    normalized = normalize_database_url(url)
    assert normalized == (
        "postgresql+psycopg://postgres:secret@example-db.invalid:33051/afl_production?sslmode=require"
    )
    parsed = make_url(normalized)
    assert parsed.port == 33051
    assert parsed.query.get("sslmode") == "require"


def test_settings_normalizes_database_url_on_construction():
    settings = Settings(database_url="postgresql://user:pw@host:5432/db", _env_file=None)
    assert settings.database_url == "postgresql+psycopg://user:pw@host:5432/db"


def test_startup_engine_uses_psycopg_for_a_bare_provider_url():
    """Mirrors app/database.py's exact `create_engine(settings.database_url,
    ...)` call - proves the real startup path resolves to Psycopg 3 for a
    bare provider-issued URL (the shape that broke the first production
    Live Cycle run), without opening a real network connection
    (create_engine is lazy - .dialect.driver is available immediately)."""
    settings = Settings(database_url="postgresql://user:pw@layerbase-host:33051/afl_production?sslmode=require", _env_file=None)
    engine = create_engine(settings.database_url)
    try:
        assert engine.dialect.driver == "psycopg"
    finally:
        engine.dispose()


def test_settings_leaves_sqlite_default_unchanged(monkeypatch):
    # CI's postgres-integration job sets a real DATABASE_URL env var - clear
    # it for this test so the class default is what's actually exercised,
    # not whatever ambient environment happens to be running the suite.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.database_url == "sqlite:///./afl.db"
