"""Shared pytest fixtures.

Tests never touch the real dev database (afl.db). Each test gets a fresh
in-memory SQLite database with the full schema applied via create_all(), and
the FastAPI app's get_db dependency is overridden to use it.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (populates Base.metadata)
from app.database import Base, get_db
from app.main import app


@pytest.fixture()
def db_session():
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


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
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
