"""API-key authentication dependency for the external B2B pricing API.

`Depends(require_api_key)` mirrors `Depends(get_db)`'s existing shape —
this is exactly the design already described (before this module existed)
in docs/API_USAGE.md's Authentication section.

Local development bypass: when `settings.app_env == "local"` (the SAME
setting that already loosens CORS in app/main.py — reused, not a new
config concept) and no `X-API-Key` header is present, the request
authenticates as a real, lazily-created `local-dev` ApiConsumer rather than
failing. This keeps `frontend/src/pages/B2BDemoPage.tsx` and every existing
test working with zero changes, while a request that DOES supply a key
(even in local mode) is validated for real — so auth/rate-limit failure
paths remain fully testable locally. Any non-local environment always
requires a real, valid key.
"""

from datetime import datetime, timezone

from fastapi import Depends, Request, Response
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api_platform.errors import AuthenticationError, RateLimitError
from app.api_platform.keys import hash_key
from app.api_platform.rate_limit import check_rate_limit
from app.config import Settings, get_settings
from app.database import get_db
from app.models import CONSUMER_STATUS_ACTIVE, KEY_STATUS_ACTIVE, ApiConsumer, ApiKey

API_KEY_HEADER = "X-API-Key"
# auto_error=False: a missing key must fall through to the local-dev-bypass
# check below, not immediately 403 - the actual accept/reject decision is
# still made explicitly in require_api_key. This declaration's only job is
# making the header show up correctly in the generated OpenAPI schema/docs.
_api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False, description="Per-consumer API key. Not required in local development.")
LOCAL_DEV_CONSUMER_NAME = "local-dev"
LOCAL_DEV_RATE_LIMIT_PER_MINUTE = 1000
LOCAL_DEV_DAILY_QUOTA = 100_000


def _get_or_create_local_dev_consumer(db: Session) -> ApiConsumer:
    consumer = db.scalar(select(ApiConsumer).where(ApiConsumer.name == LOCAL_DEV_CONSUMER_NAME))
    if consumer is not None:
        return consumer
    consumer = ApiConsumer(
        name=LOCAL_DEV_CONSUMER_NAME, status=CONSUMER_STATUS_ACTIVE,
        rate_limit_per_minute=LOCAL_DEV_RATE_LIMIT_PER_MINUTE, daily_quota=LOCAL_DEV_DAILY_QUOTA,
    )
    db.add(consumer)
    db.commit()
    db.refresh(consumer)
    return consumer


def _authenticate_with_key(db: Session, raw_key: str) -> ApiConsumer:
    api_key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(raw_key)))
    if api_key is None:
        raise AuthenticationError("Invalid API key.")
    if api_key.status != KEY_STATUS_ACTIVE:
        raise AuthenticationError("This API key has been revoked.")
    consumer = db.get(ApiConsumer, api_key.consumer_id)
    if consumer is None or consumer.status != CONSUMER_STATUS_ACTIVE:
        raise AuthenticationError("This API consumer is disabled.")
    api_key.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return consumer


def require_api_key(
    request: Request, response: Response, raw_key: str | None = Depends(_api_key_scheme),
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
) -> ApiConsumer:

    if raw_key is None:
        if settings.app_env != "local":
            raise AuthenticationError(f"Missing API key. Provide it via the {API_KEY_HEADER} header.")
        consumer = _get_or_create_local_dev_consumer(db)
    else:
        consumer = _authenticate_with_key(db, raw_key)

    # Set before the rate-limit check (not after) so a REJECTED request's
    # usage-log row is still attributed to this consumer, not logged as
    # consumer_id=None - otherwise a consumer's own rate-limit rejections
    # would be invisible to their own usage stats.
    request.state.consumer_id = consumer.id
    request.state.consumer_name = consumer.name

    now = datetime.now(timezone.utc)
    rate_status = check_rate_limit(db, consumer, now)
    response.headers["X-RateLimit-Limit"] = str(rate_status.limit_per_minute)
    response.headers["X-RateLimit-Remaining"] = str(max(rate_status.remaining_this_minute, 0))
    response.headers["X-RateLimit-Daily-Quota"] = str(rate_status.daily_quota)
    response.headers["X-RateLimit-Daily-Remaining"] = str(max(rate_status.remaining_today, 0))

    if not rate_status.allowed:
        request.state.rate_limited = True
        raise RateLimitError(
            f"Rate limit exceeded ({rate_status.reason}). Try again later.",
            retry_after_seconds=rate_status.retry_after_seconds,
            details={"reason": rate_status.reason},
        )

    return consumer
