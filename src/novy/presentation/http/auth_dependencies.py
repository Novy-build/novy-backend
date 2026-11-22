"""Transport-level guards for unsafe requests and session cookie access.

These helpers never resolve a session or touch protected state. They only
validate the browser-supplied origin and CSRF material before a use case runs.
"""

import hmac
from typing import Protocol, cast

from fastapi import Request

from novy.application.builder_sessions import BuilderSessionService
from novy.infrastructure.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    PREAUTH_COOKIE_NAME,
    is_canonical_token,
)
from novy.presentation.http.builder_session_cookie import BUILDER_SESSION_COOKIE
from novy.presentation.http.errors import api_error

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOCAL_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


class OriginSettings(Protocol):
    @property
    def environment(self) -> str: ...

    @property
    def allowed_origins(self) -> str: ...


def allowed_origins(settings: OriginSettings) -> tuple[str, ...]:
    configured = [entry.strip().rstrip("/") for entry in settings.allowed_origins.split(",")]
    configured = [entry for entry in configured if entry]
    if not configured and settings.environment in ("development", "test"):
        configured = list(_LOCAL_ORIGINS)
    return tuple(dict.fromkeys(configured))


async def require_unsafe_request_guards(request: Request) -> None:
    if request.method not in _UNSAFE_METHODS:
        return

    settings = getattr(request.app.state, "settings", None)
    origins = allowed_origins(settings) if settings is not None else ()
    origin = request.headers.get("origin")
    if origin is None or origin.rstrip("/") not in origins:
        # In non-production environments, expose the received origin so operators
        # can configure NOVY_ALLOWED_ORIGINS without guessing. Production stays opaque.
        details: dict[str, object] | None = None
        if settings is not None and settings.environment != "production":
            details = {
                "receivedOrigin": origin,
                "expectedOrigins": list(origins),
            }
        raise api_error(403, "origin_not_allowed", "Origin not allowed", details=details)

    service = getattr(request.app.state, "csrf_service", None)
    if service is None:
        raise api_error(503, "auth_unavailable", "Security service unavailable")

    header_token = request.headers.get(CSRF_HEADER_NAME)
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if (
        not header_token
        or not cookie_token
        or not service.is_canonical_token(header_token)
        or not service.is_canonical_token(cookie_token)
        or not hmac.compare_digest(header_token, cookie_token)
    ):
        raise api_error(403, "csrf_failed", "CSRF validation failed")

    preauth_state = request.cookies.get(PREAUTH_COOKIE_NAME)
    if preauth_state and service.verify_preauth(preauth_state, header_token):
        return

    credential = request.cookies.get(BUILDER_SESSION_COOKIE)
    if credential and service.verify_session(credential, header_token):
        return

    raise api_error(403, "csrf_failed", "CSRF validation failed")


def get_session_credential(request: Request) -> str:
    credential = request.cookies.get(BUILDER_SESSION_COOKIE)
    if not credential or len(credential) != 43 or not is_canonical_token(credential):
        raise api_error(401, "session_required", "Session required")
    return credential


def require_session_service(request: Request) -> BuilderSessionService:
    service = getattr(request.app.state, "builder_session_service", None)
    if service is None:
        raise api_error(503, "auth_unavailable", "Authentication unavailable")
    return cast(BuilderSessionService, service)
