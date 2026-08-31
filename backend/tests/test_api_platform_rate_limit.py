from datetime import datetime, timedelta, timezone

from app.api_platform.rate_limit import check_rate_limit
from app.models import ApiConsumer, ApiUsageRecord

NOW = datetime.now(timezone.utc)


def _consumer(db, *, name="Test Consumer", rate_limit_per_minute=5, daily_quota=10):
    c = ApiConsumer(name=name, status="active", rate_limit_per_minute=rate_limit_per_minute, daily_quota=daily_quota)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _record(db, consumer_id, recorded_at):
    db.add(ApiUsageRecord(
        request_id="r", consumer_id=consumer_id, endpoint="/api/v1/pricing/afl/current-round", method="GET",
        status_code=200, latency_ms=5.0, recorded_at=recorded_at,
    ))


class TestCheckRateLimit:
    def test_allowed_when_under_both_limits(self, db_session):
        consumer = _consumer(db_session)
        result = check_rate_limit(db_session, consumer, NOW)
        assert result.allowed is True
        assert result.reason is None

    def test_rejected_when_per_minute_limit_hit(self, db_session):
        consumer = _consumer(db_session, rate_limit_per_minute=3)
        for _ in range(3):
            _record(db_session, consumer.id, NOW - timedelta(seconds=10))
        db_session.commit()

        result = check_rate_limit(db_session, consumer, NOW)

        assert result.allowed is False
        assert result.reason == "per_minute"
        assert result.retry_after_seconds == 60

    def test_requests_older_than_a_minute_do_not_count(self, db_session):
        consumer = _consumer(db_session, rate_limit_per_minute=3)
        for _ in range(3):
            _record(db_session, consumer.id, NOW - timedelta(minutes=5))
        db_session.commit()

        result = check_rate_limit(db_session, consumer, NOW)

        assert result.allowed is True

    def test_rejected_when_daily_quota_hit_even_under_minute_limit(self, db_session):
        consumer = _consumer(db_session, rate_limit_per_minute=100, daily_quota=5)
        for _ in range(5):
            _record(db_session, consumer.id, NOW - timedelta(hours=2))
        db_session.commit()

        result = check_rate_limit(db_session, consumer, NOW)

        assert result.allowed is False
        assert result.reason == "daily"
        assert result.retry_after_seconds == 3600

    def test_requests_older_than_24h_do_not_count_toward_daily_quota(self, db_session):
        consumer = _consumer(db_session, daily_quota=5)
        for _ in range(5):
            _record(db_session, consumer.id, NOW - timedelta(hours=25))
        db_session.commit()

        result = check_rate_limit(db_session, consumer, NOW)

        assert result.allowed is True

    def test_separate_consumers_are_isolated(self, db_session):
        busy = _consumer(db_session, name="Busy Consumer", rate_limit_per_minute=1)
        quiet = _consumer(db_session, name="Quiet Consumer", rate_limit_per_minute=1)
        _record(db_session, busy.id, NOW - timedelta(seconds=5))
        db_session.commit()

        busy_result = check_rate_limit(db_session, busy, NOW)
        quiet_result = check_rate_limit(db_session, quiet, NOW)

        assert busy_result.allowed is False
        assert quiet_result.allowed is True

    def test_remaining_counts_reflect_usage(self, db_session):
        consumer = _consumer(db_session, rate_limit_per_minute=5, daily_quota=10)
        _record(db_session, consumer.id, NOW - timedelta(seconds=5))
        _record(db_session, consumer.id, NOW - timedelta(seconds=5))
        db_session.commit()

        result = check_rate_limit(db_session, consumer, NOW)

        assert result.remaining_this_minute == 2  # 5 - 2 used - 1 for this request
        assert result.remaining_today == 7
