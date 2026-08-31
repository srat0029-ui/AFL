from sqlalchemy import select

from app.api_platform.keys import generate_api_key, hash_key, key_prefix
from app.config import Settings, get_settings
from app.main import app
from app.models import ApiConsumer, ApiKey, ApiUsageRecord

PRICING_URL = "/api/v1/pricing/afl/current-round"
INTERNAL_URL = "/api/health"


def _non_local_settings() -> Settings:
    return Settings(app_env="production")


def _create_consumer_and_key(db, *, rate_limit_per_minute=60, daily_quota=5000):
    consumer = ApiConsumer(name="Usage Test Consumer", status="active", rate_limit_per_minute=rate_limit_per_minute, daily_quota=daily_quota)
    db.add(consumer)
    db.commit()
    db.refresh(consumer)
    raw_key = generate_api_key()
    db.add(ApiKey(consumer_id=consumer.id, key_hash=hash_key(raw_key), key_prefix=key_prefix(raw_key), status="active"))
    db.commit()
    return consumer, raw_key


class TestUsageLogging:
    def test_b2b_request_produces_exactly_one_usage_record(self, client, db_session):
        client.get(PRICING_URL)
        records = db_session.scalars(select(ApiUsageRecord)).all()
        assert len(records) == 1
        assert records[0].endpoint == PRICING_URL
        assert records[0].method == "GET"
        assert records[0].status_code == 200
        assert records[0].latency_ms >= 0
        assert records[0].release_sha  # connects this request to the exact code version that served it

    def test_internal_route_produces_no_usage_record(self, client, db_session):
        client.get(INTERNAL_URL)
        records = db_session.scalars(select(ApiUsageRecord)).all()
        assert records == []

    def test_usage_record_never_contains_the_raw_api_key(self, client, db_session):
        consumer, raw_key = _create_consumer_and_key(db_session)
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            client.get(PRICING_URL, headers={"X-API-Key": raw_key})
        finally:
            del app.dependency_overrides[get_settings]
        record = db_session.scalar(select(ApiUsageRecord))
        # Serialize every field to a string and confirm the raw key never appears anywhere.
        rendered = " ".join(str(v) for v in vars(record).values())
        assert raw_key not in rendered

    def test_failed_auth_is_still_logged(self, client, db_session):
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            client.get(PRICING_URL, headers={"X-API-Key": "afl_bogus"})
        finally:
            del app.dependency_overrides[get_settings]
        record = db_session.scalar(select(ApiUsageRecord))
        assert record is not None
        assert record.status_code == 401
        assert record.consumer_id is None


class TestRateLimitEndToEnd:
    def test_requests_within_limit_all_succeed(self, client, db_session):
        _consumer, raw_key = _create_consumer_and_key(db_session, rate_limit_per_minute=5)
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            for _ in range(5):
                resp = client.get(PRICING_URL, headers={"X-API-Key": raw_key})
                assert resp.status_code == 200
        finally:
            del app.dependency_overrides[get_settings]

    def test_exceeding_per_minute_limit_returns_429_with_standard_headers(self, client, db_session):
        _consumer, raw_key = _create_consumer_and_key(db_session, rate_limit_per_minute=3)
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            for _ in range(3):
                assert client.get(PRICING_URL, headers={"X-API-Key": raw_key}).status_code == 200
            resp = client.get(PRICING_URL, headers={"X-API-Key": raw_key})
        finally:
            del app.dependency_overrides[get_settings]

        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        body = resp.json()
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert "request_id" in body

    def test_rate_limited_request_is_flagged_in_usage_record(self, client, db_session):
        consumer, raw_key = _create_consumer_and_key(db_session, rate_limit_per_minute=1)
        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            client.get(PRICING_URL, headers={"X-API-Key": raw_key})
            client.get(PRICING_URL, headers={"X-API-Key": raw_key})
        finally:
            del app.dependency_overrides[get_settings]

        records = db_session.scalars(select(ApiUsageRecord).order_by(ApiUsageRecord.id)).all()
        assert len(records) == 2
        assert records[0].rate_limited is False
        assert records[1].rate_limited is True
        assert records[1].status_code == 429
        # A rejected request must still be attributed to the consumer whose
        # key was used - otherwise their own rate-limit rejections would be
        # invisible to their own usage stats (regression: consumer_id was
        # previously only set on request.state AFTER the rate-limit check).
        assert records[0].consumer_id == consumer.id
        assert records[1].consumer_id == consumer.id

    def test_separate_consumers_are_rate_limited_independently(self, client, db_session):
        _c1, key1 = _create_consumer_and_key(db_session, rate_limit_per_minute=1)
        consumer2 = ApiConsumer(name="Other Consumer", status="active", rate_limit_per_minute=1, daily_quota=5000)
        db_session.add(consumer2)
        db_session.commit()
        raw_key2 = generate_api_key()
        db_session.add(ApiKey(consumer_id=consumer2.id, key_hash=hash_key(raw_key2), key_prefix=key_prefix(raw_key2), status="active"))
        db_session.commit()

        app.dependency_overrides[get_settings] = _non_local_settings
        try:
            client.get(PRICING_URL, headers={"X-API-Key": key1})  # consumer 1 uses its one request
            resp_c1_second = client.get(PRICING_URL, headers={"X-API-Key": key1})
            resp_c2_first = client.get(PRICING_URL, headers={"X-API-Key": raw_key2})
        finally:
            del app.dependency_overrides[get_settings]

        assert resp_c1_second.status_code == 429
        assert resp_c2_first.status_code == 200  # unaffected by consumer 1's limit
