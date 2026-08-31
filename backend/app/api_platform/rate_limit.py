"""Per-consumer rate limiting — a simple, defensible, database-backed
implementation, not a distributed one. Counts rows already written to
`ApiUsageRecord` (the same table usage logging writes to — one table
serves both purposes, rather than a second piece of rate-limit-specific
state) over a rolling window, and rejects BEFORE any pricing computation
runs if the consumer is already over either limit.

Honest limitation, stated rather than hidden: this is a single-process,
single-database count. It is correct for this project's real deployment
shape (one API process, one database) but would need a shared store
(Redis, or a DB with tighter isolation guarantees under real concurrent
load) to stay exactly accurate across multiple API processes — not
introduced here because nothing about this project's actual scale needs it
yet (item 2's explicit instruction not to add distributed infrastructure
unless genuinely necessary).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ApiConsumer, ApiUsageRecord

DAILY_WINDOW = timedelta(hours=24)
MINUTE_WINDOW = timedelta(minutes=1)


@dataclass(frozen=True)
class RateLimitStatus:
    allowed: bool
    limit_per_minute: int
    remaining_this_minute: int
    daily_quota: int
    remaining_today: int
    retry_after_seconds: int
    reason: str | None  # "per_minute" | "daily" | None


def _count_since(db: Session, consumer_id: int, since: datetime) -> int:
    return db.scalar(
        select(func.count()).select_from(ApiUsageRecord).where(
            ApiUsageRecord.consumer_id == consumer_id, ApiUsageRecord.recorded_at >= since,
        )
    ) or 0


def check_rate_limit(db: Session, consumer: ApiConsumer, now: datetime) -> RateLimitStatus:
    minute_count = _count_since(db, consumer.id, now - MINUTE_WINDOW)
    daily_count = _count_since(db, consumer.id, now - DAILY_WINDOW)

    remaining_minute = max(consumer.rate_limit_per_minute - minute_count, 0)
    remaining_daily = max(consumer.daily_quota - daily_count, 0)

    if minute_count >= consumer.rate_limit_per_minute:
        return RateLimitStatus(
            allowed=False, limit_per_minute=consumer.rate_limit_per_minute, remaining_this_minute=0,
            daily_quota=consumer.daily_quota, remaining_today=remaining_daily, retry_after_seconds=60, reason="per_minute",
        )
    if daily_count >= consumer.daily_quota:
        return RateLimitStatus(
            allowed=False, limit_per_minute=consumer.rate_limit_per_minute, remaining_this_minute=remaining_minute,
            daily_quota=consumer.daily_quota, remaining_today=0, retry_after_seconds=3600, reason="daily",
        )
    return RateLimitStatus(
        allowed=True, limit_per_minute=consumer.rate_limit_per_minute, remaining_this_minute=remaining_minute - 1,
        daily_quota=consumer.daily_quota, remaining_today=remaining_daily - 1, retry_after_seconds=0, reason=None,
    )
