"""Tests for the per-request/short-TTL caches (Current Context stage's
performance follow-up) — the exact bug a full test-suite run caught the
first time this was added: results leaking across DIFFERENT database
engines because the cache key didn't account for which database was
actually queried."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.player_modelling.request_cache import cached, cached_with_ttl, clear_ttl_cache


def _new_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_cached_computes_once_per_session_for_same_key(db_session):
    calls = []

    def compute():
        calls.append(1)
        return "value"

    assert cached(db_session, ("k",), compute) == "value"
    assert cached(db_session, ("k",), compute) == "value"
    assert len(calls) == 1


def test_cached_different_keys_recompute():
    db = _new_session()
    calls = []

    def make(v):
        def compute():
            calls.append(v)
            return v
        return compute

    assert cached(db, ("a",), make("A")) == "A"
    assert cached(db, ("b",), make("B")) == "B"
    assert len(calls) == 2
    db.close()


def test_cached_with_ttl_does_not_leak_across_different_engines():
    """The exact regression this module exists to prevent: two isolated
    DBs (as in the real test suite) must never see each other's cached
    result for the same key."""
    clear_ttl_cache()
    db_a = _new_session()
    db_b = _new_session()
    try:
        result_a = cached_with_ttl(db_a, ("shared_key",), lambda: "result-from-A")
        result_b = cached_with_ttl(db_b, ("shared_key",), lambda: "result-from-B")
        assert result_a == "result-from-A"
        assert result_b == "result-from-B"
    finally:
        db_a.close()
        db_b.close()


def test_cached_with_ttl_reuses_value_within_ttl():
    clear_ttl_cache()
    db = _new_session()
    calls = []

    def compute():
        calls.append(1)
        return "v"

    try:
        assert cached_with_ttl(db, ("k",), compute, ttl_seconds=60) == "v"
        assert cached_with_ttl(db, ("k",), compute, ttl_seconds=60) == "v"
        assert len(calls) == 1
    finally:
        db.close()


def test_cached_with_ttl_expires():
    clear_ttl_cache()
    db = _new_session()
    calls = []

    def compute():
        calls.append(1)
        return "v"

    try:
        assert cached_with_ttl(db, ("k",), compute, ttl_seconds=-1) == "v"  # already expired
        assert cached_with_ttl(db, ("k",), compute, ttl_seconds=-1) == "v"
        assert len(calls) == 2
    finally:
        db.close()


def test_clear_ttl_cache_forces_recompute():
    db = _new_session()
    calls = []

    def compute():
        calls.append(1)
        return "v"

    try:
        cached_with_ttl(db, ("k2",), compute, ttl_seconds=60)
        clear_ttl_cache()
        cached_with_ttl(db, ("k2",), compute, ttl_seconds=60)
        assert len(calls) == 2
    finally:
        db.close()
