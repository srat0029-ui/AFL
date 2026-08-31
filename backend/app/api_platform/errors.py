"""A consistent external API error contract — before this, every one of
~40 routes in this codebase raised `HTTPException` with a status code and a
plain string `detail`, entirely manually, with no shared shape and no
request correlation. This module adds that shape WITHOUT requiring every
existing route to change: `register_exception_handlers` registers handlers
for `ApiError` (new, typed, precise error codes), `HTTPException` (every
pre-existing route's own raise sites — reformatted for free, no code
changes needed there), `RequestValidationError` (FastAPI's automatic 422s
for malformed request bodies), and a bare `Exception` catch-all (never
leaks a stack trace or internal detail to a client).

Every error response has the same JSON shape:
    {"error_code": "...", "message": "...", "request_id": "...", "details": {...} | null}
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

ERROR_AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
ERROR_RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
ERROR_VALIDATION_ERROR = "VALIDATION_ERROR"
ERROR_UNSUPPORTED_MARKET = "UNSUPPORTED_MARKET"
ERROR_NOT_FOUND = "NOT_FOUND"
ERROR_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
ERROR_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
ERROR_SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
ERROR_INTERNAL_ERROR = "INTERNAL_ERROR"
ERROR_GENERIC = "ERROR"

# Coarse fallback mapping for the ~40 pre-existing routes that raise a plain
# HTTPException(status_code, detail) with no error_code of their own -
# still gets a consistent envelope + request_id, just a less precise code
# than a purpose-raised ApiError subclass would carry.
_STATUS_TO_CODE = {
    400: ERROR_VALIDATION_ERROR,
    401: ERROR_AUTHENTICATION_FAILED,
    403: ERROR_AUTHENTICATION_FAILED,
    404: ERROR_NOT_FOUND,
    409: ERROR_VALIDATION_ERROR,
    422: ERROR_VALIDATION_ERROR,
    429: ERROR_RATE_LIMIT_EXCEEDED,
    500: ERROR_INTERNAL_ERROR,
    503: ERROR_SERVICE_UNAVAILABLE,
}


class ApiError(Exception):
    """Base for every purpose-raised external-API error. Route/dependency
    code should raise a specific subclass below rather than this directly."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    error_code: str = ERROR_GENERIC

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


class AuthenticationError(ApiError):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = ERROR_AUTHENTICATION_FAILED


class RateLimitError(ApiError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    error_code = ERROR_RATE_LIMIT_EXCEEDED

    def __init__(self, message: str, *, retry_after_seconds: int, details: dict | None = None):
        super().__init__(message, details=details)
        self.retry_after_seconds = retry_after_seconds


class ValidationError(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = ERROR_VALIDATION_ERROR


class UnsupportedMarketError(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = ERROR_UNSUPPORTED_MARKET


class NotFoundError(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = ERROR_NOT_FOUND


class DataUnavailableError(ApiError):
    """Data exists but is insufficient/too stale to price confidently -
    distinct from ModelUnavailableError (the model itself can't be built)."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = ERROR_DATA_UNAVAILABLE


class ModelUnavailableError(ApiError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = ERROR_MODEL_UNAVAILABLE


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _error_body(error_code: str, message: str, request_id: str, details: dict | None = None) -> dict:
    return {"error_code": error_code, "message": message, "request_id": request_id, "details": details}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after_seconds)} if isinstance(exc, RateLimitError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.error_code, exc.message, _request_id(request), exc.details),
            headers=headers,
        )

    @app.exception_handler(HTTPException)
    async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ERROR_GENERIC)
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(
            status_code=exc.status_code, content=_error_body(code, message, _request_id(request)), headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # jsonable_encoder is required here, not optional: Pydantic v2's
        # RequestValidationError.errors() can embed the raw exception object
        # that triggered a custom validator (e.g. a Field constraint) inside
        # each error's `ctx` dict - plain json.dumps chokes on that directly.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body(
                ERROR_VALIDATION_ERROR, "The request body failed validation.", _request_id(request),
                details={"errors": jsonable_encoder(exc.errors())},
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        # Full detail server-side only, correlated by request_id - never
        # sent to the client (item 7/15: no stack traces, no DB details externally).
        logger.error("unhandled_exception request_id=%s path=%s error=%s", request_id, request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(ERROR_INTERNAL_ERROR, "An internal error occurred.", request_id),
        )
