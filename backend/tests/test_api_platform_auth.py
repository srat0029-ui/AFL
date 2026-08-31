"""End-to-end auth tests via the real HTTP client — exercising
Depends(require_api_key) exactly as a real external consumer would hit it,
not by calling the dependency function directly."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.api_platform.keys import generate_api_key, hash_key, key_prefix
from app.config import Settings, get_settings
from app.main import app
from app.models import ApiConsumer, ApiKey, ApiUsageRecord

NOW = datetime.now(timezone.utc)
PRICING_URL = "/api/v1/pricing/afl/current-round"


def _non_local_settings() -> Settings:
    return Settings(app_env="production")


def _create_consumer_and_key(db, *, consumer_status="active", key_status="active", rate_limit_per_minute=60, daily_quota=5000):
    consumer = ApiConsumer(name="Test Consumer", status=consumer_status, rate_limit_per_minute=rate_limit_per_minute, daily_quota=daily_quota)
    db.add(consumer)
    db.commit()
    db.refresh(consumer)
    raw_key = generate_api_key()
    key = ApiKey(consumer_id=consumer.id, key_hash=hash_key(raw_key), key_prefix=key_prefix(raw_key), status=key_status)
    db.add(key)
    db.commit()
    return consumer, raw_key


class TestLocalDevBypass:
    def test_missing_key_works_in_local_dev(self, client, db_session):
        # No override - default Settings() has app_env="local".
        resp = client.get(PRICING_URL)
        assert resp.status_code == 200

    def test_local_dev_consumer_is_created_lazily(self, client, db_session):
        client.get(PRICING_URL)
        consumer = db_session.scalar(select(ApiConsumer).where(ApiConsumer.name == "local-dev"))
        assert consumer is not None
        assert consumer.status == "active"


class TestNonLocalRequiresKey:
    def test_missing_key_fails_outside_local_dev(self, client, db_session):
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            resp = client.get(PRICING_URL)
        finally:
            del app.dependency_overrides[get_settings]
        assert resp.status_code == 401
        body = resp.json()
        assert body["error_code"] == "AUTHENTICATION_FAILED"
        assert "request_id" in body

    def test_valid_key_works_outside_local_dev(self, client, db_session):
        _consumer, raw_key = _create_consumer_and_key(db_session)
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            resp = client.get(PRICING_URL, headers={"X-API-Key": raw_key})
        finally:
            del app.dependency_overrides[get_settings]
        assert resp.status_code == 200

    def test_invalid_key_fails(self, client, db_session):
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            resp = client.get(PRICING_URL, headers={"X-API-Key": "afl_not_a_real_key"})
        finally:
            del app.dependency_overrides[get_settings]
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTHENTICATION_FAILED"

    def test_revoked_key_fails(self, client, db_session):
        _consumer, raw_key = _create_consumer_and_key(db_session, key_status="revoked")
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            resp = client.get(PRICING_URL, headers={"X-API-Key": raw_key})
        finally:
            del app.dependency_overrides[get_settings]
        assert resp.status_code == 401

    def test_disabled_consumer_cannot_authenticate(self, client, db_session):
        _consumer, raw_key = _create_consumer_and_key(db_session, consumer_status="disabled")
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            resp = client.get(PRICING_URL, headers={"X-API-Key": raw_key})
        finally:
            del app.dependency_overrides[get_settings]
        assert resp.status_code == 401

    def test_valid_key_even_when_supplied_in_local_mode_is_fully_validated(self, client, db_session):
        # Local mode only bypasses a MISSING key - a supplied key is always
        # validated for real, so failure paths remain testable locally.
        resp = client.get(PRICING_URL, headers={"X-API-Key": "afl_bogus"})
        assert resp.status_code == 401


class TestPlaintextNeverPersisted:
    def test_raw_key_is_never_stored_anywhere_in_the_db(self, client, db_session):
        _consumer, raw_key = _create_consumer_and_key(db_session)
        stored = db_session.scalar(select(ApiKey))
        assert raw_key not in (stored.key_hash or "")
        assert raw_key not in (stored.key_prefix or "")
        assert stored.key_hash != raw_key

    def test_last_used_at_updates_on_successful_auth(self, client, db_session):
        consumer, raw_key = _create_consumer_and_key(db_session)
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            client.get(PRICING_URL, headers={"X-API-Key": raw_key})
        finally:
            del app.dependency_overrides[get_settings]
        db_session.expire_all()
        key = db_session.scalar(select(ApiKey).where(ApiKey.consumer_id == consumer.id))
        assert key.last_used_at is not None


class TestRateLimitHeaders:
    def test_successful_response_carries_rate_limit_headers(self, client, db_session):
        resp = client.get(PRICING_URL)
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Daily-Quota" in resp.headers
        assert "X-RateLimit-Daily-Remaining" in resp.headers
