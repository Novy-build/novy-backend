"""Public error envelope and centralized FastAPI exception handlers.

Handlers deliberately expose only stable codes and safe messages. Provider
details, raw request bodies, validation inputs, and tracebacks never cross this
boundary.
"""

import re
import uuid
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler

DEFAULT_MESSAGE = "Request could not be completed"
_CODE = re.compile(r"[a-z0-9_]{1,64}\Z", re.ASCII)

_HTTP_CODES: dict[int, tuple[str, str]] = {
    400: ("bad_request", "Bad request"),
    401: ("unauthorized", "Authentication required"),
    403: ("forbidden", "Access denied"),
    404: ("not_found", "Resource not found"),
    405: ("method_not_allowed", "Method not allowed"),
    409: ("conflict", "Request conflict"),
    413: ("payload_too_large", "Request payload too large"),
    415: ("unsupported_media_type", "Unsupported media type"),
    429: ("rate_limited", "Too many requests"),
}


class ApiError(Exception):
    """A safe, application-owned error that can be rendered to clients."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str = DEFAULT_MESSAGE,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        self.status_code = (
            status_code if type(status_code) is int and 400 <= status_code <= 599 else 500
        )
        self.code = (
            code if type(code) is str and _CODE.fullmatch(code) is not None else "internal_error"
        )
        self.message = message if type(message) is str and message.strip() else DEFAULT_MESSAGE
        self.details = details if type(details) is dict else None
        super().__init__(self.message)


def api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> ApiError:
    return ApiError(status_code, code, message, details=details)


def _envelope(code: str, message: str, details: dict[str, object] | None) -> dict[str, object]:
    error: dict[str, object] = {
        "code": code,
        "message": message,
        "requestId": uuid.uuid4().hex,
    }
    if details is not None:
        error["details"] = details
    return {"error": error}


def _response(
    status_code: int, code: str, message: str, details: dict[str, object] | None
) -> JSONResponse:
    return JSONResponse(
        _envelope(code, message, details),
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return _response(exc.status_code, exc.code, exc.message, exc.details)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    issues: list[dict[str, object]] = []
    errors = getattr(exc, "errors", None)
    raw_errors: object = []
    if callable(errors):
        try:
            raw_errors = errors()
        except Exception:
            raw_errors = []
    if isinstance(raw_errors, (list, tuple)):
        for item in raw_errors:
            if not isinstance(item, dict):
                continue
            raw_location = item.get("loc")
            location = (
                [str(part) for part in raw_location]
                if isinstance(raw_location, (list, tuple))
                else []
            )
            raw_message = item.get("msg")
            message = (
                raw_message if type(raw_message) is str and raw_message.strip() else "Invalid value"
            )
            issues.append(
                {
                    "field": location[-1] if location else "",
                    "location": location,
                    "message": message,
                }
            )
    if not issues:
        issues = [{"field": "", "location": [], "message": "Invalid request"}]
    return _response(400, "validation_error", "Request validation failed", {"issues": issues})


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    status_code = exc.status_code if type(exc.status_code) is int else 500
    code, message = _HTTP_CODES.get(status_code, ("http_error", "Request failed"))
    return _response(status_code, code, message, None)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return _response(500, "internal_error", "An unexpected error occurred", None)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, cast(ExceptionHandler, api_error_handler))
    app.add_exception_handler(
        RequestValidationError, cast(ExceptionHandler, validation_error_handler)
    )
    app.add_exception_handler(
        StarletteHTTPException, cast(ExceptionHandler, http_exception_handler)
    )
    app.add_exception_handler(Exception, cast(ExceptionHandler, unhandled_error_handler))
