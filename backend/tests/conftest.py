"""Shared pytest fixtures.

Tests never touch the real dev database (afl.db). By default each test gets
a fresh in-memory SQLite database with the full schema applied via
create_all(), and the FastAPI app's get_db dependency is overridden to use
it.

Setting TEST_DATABASE_URL (e.g. to a postgresql+psycopg://... URL) points
the SAME fixture, and therefore every test that uses it, at a real Postgres
instance instead — this is how CI's postgres-integration job and a local
`docker compose` Postgres actually exercise real test assertions against a
real Postgres dialect, without a second, parallel test suite to maintain.
The default (unset) path is unchanged from before this existed.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (populates Base.metadata)
from app.database import Base, get_db
from app.main import app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture()
def db_session():
    if TEST_DATABASE_URL:
        engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    else:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


class _NoCloseSessionProxy:
    """Wraps db_session so app/api_platform/request_context.py's middleware
    (which opens its OWN session via a direct SessionLocal() call, entirely
    outside FastAPI's dependency-injection system - middleware runs after
    the request's own get_db-scoped session is already torn down) writes to
    the SAME in-memory test database as everything else in the test,
    instead of the real dev database (afl.db). `close()` is a no-op since
    db_session's own fixture teardown owns the real close."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self) -> None:
        pass


@pytest.fixture()
def client(db_session, monkeypatch):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr("app.api_platform.request_context.SessionLocal", lambda: _NoCloseSessionProxy(db_session))
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_backtests_route_cache():
    """app/api/routes/backtests.py process-level-caches its three most
    expensive comparison endpoints (poisson-revision was observed taking
    ~33s recomputed live - see that module's cache comment) for the life
    of the real server process. Each test gets its own throwaway in-memory
    DB via db_session above, so that cache must be cleared before/after
    every test - otherwise a later test could silently read a cached
    result computed against an earlier test's already-torn-down database."""
    import app.api.routes.backtests as backtests_route

    backtests_route._logistic_comparison_cache = None
    backtests_route._boosting_comparison_cache = None
    backtests_route._poisson_revision_cache = None
    yield
    backtests_route._logistic_comparison_cache = None
    backtests_route._boosting_comparison_cache = None
    backtests_route._poisson_revision_cache = None
