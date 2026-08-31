"""Request correlation + usage logging middleware.

Every response (from every route, internal or external) gets an
`X-Request-ID` header — a client that hits an error can report this ID and
it's directly greppable in server logs and (for the B2B surface) in
`ApiUsageRecord`. This is deliberately a single clean correlation-ID
implementation, not a distributed tracing platform (item 4's explicit
boundary).

Usage logging (writing an `ApiUsageRecord` row) is scoped to the external
B2B prefixes ONLY (`/api/v1/pricing`, `/api/v1/market-intelligence`) - every
internal product route the frontend calls would otherwise dwarf the table
with irrelevant volume. This middleware uses its OWN short-lived DB
session, never the request's `Depends(get_db)` session - by the time
middleware post-processing runs (after `call_next` returns), FastAPI has
already torn down that session as part of the request's own dependency
cleanup.
"""

import logging
import time
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.database import SessionLocal
from app.models import ApiUsageRecord

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
USAGE_LOGGED_PREFIXES = ("/api/v1/pricing", "/api/v1/market-intelligence")


def _should_log_usage(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in USAGE_LOGGED_PREFIXES)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()

        response = await call_next(request)

        latency_ms = (time.perf_counter() - start) * 1000.0
        response.headers[REQUEST_ID_HEADER] = request_id

        if _should_log_usage(request.url.path):
            self._log_usage(request, response, request_id, latency_ms)

        return response

    def _log_usage(self, request: Request, response: Response, request_id: str, latency_ms: float) -> None:
        db = SessionLocal()
        try:
            db.add(ApiUsageRecord(
                request_id=request_id,
                consumer_id=getattr(request.state, "consumer_id", None),
                endpoint=request.url.path,
                method=request.method,
                status_code=response.status_code,
                latency_ms=latency_ms,
                model_version=getattr(request.state, "model_version", None),
                freshness=getattr(request.state, "freshness", None),
                rate_limited=getattr(request.state, "rate_limited", False),
                recorded_at=datetime.now(timezone.utc),
            ))
            db.commit()
        except Exception:  # noqa: BLE001 - usage logging must never break the actual response
            logger.error("usage_logging.failed request_id=%s path=%s", request_id, request.url.path, exc_info=True)
            db.rollback()
        finally:
            db.close()
