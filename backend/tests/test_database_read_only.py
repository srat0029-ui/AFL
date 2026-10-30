"""DATABASE_READ_ONLY: a backend that may only read - for viewing production
data locally. Enforced by PostgreSQL itself (default_transaction_read_only),
never by the application promising not to write."""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.database import READ_ONLY_OPTION, engine_connect_args

PG_URL = "postgresql+psycopg://user:pass@db.example/app"


def test_postgres_sessions_start_read_only_when_enabled():
    assert engine_connect_args(Settings(database_url=PG_URL, database_read_only=True)) == {"options": READ_ONLY_OPTION}
    assert engine_connect_args(Settings(database_url=PG_URL, database_read_only=False)) == {}


def test_off_by_default():
    assert Settings(database_url=PG_URL).database_read_only is False


def test_sqlite_refuses_rather_than_silently_ignoring_it():
    assert engine_connect_args(Settings(database_url="sqlite:///./x.db")) == {"check_same_thread": False}
    with pytest.raises(ValueError, match="only supported for PostgreSQL"):
        engine_connect_args(Settings(database_url="sqlite:///./x.db", database_read_only=True))


def test_dashboard_reports_the_mode(client):
    assert client.get("/api/nba/outlooks/games").json()["database_read_only"] is False


@pytest.mark.skipif(not (os.environ.get("TEST_DATABASE_URL") or "").startswith("postgresql"), reason="needs a real PostgreSQL (CI)")
def test_postgres_rejects_writes_with_the_apps_connect_args(db_session):
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_engine(url, connect_args=engine_connect_args(Settings(database_url=url, database_read_only=True)))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("select count(*) from nba_teams")).scalar() is not None  # reads work
            assert conn.execute(text("show default_transaction_read_only")).scalar() == "on"
        with engine.connect() as conn, pytest.raises(DBAPIError, match="read-only transaction"):
            conn.execute(text("insert into nba_teams (name, abbreviation) values ('Read Only', 'RO')"))
    finally:
        engine.dispose()
