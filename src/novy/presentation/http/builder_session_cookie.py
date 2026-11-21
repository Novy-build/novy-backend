"""Cookie transport only; callers must authenticate and enforce CSRF and origin checks.

Set the cookie on initial issuance, not automatically on every request. Server-side
absolute expiry, idle expiry, and revocation remain authoritative.
"""

from fastapi import Response

from novy.application.builder_sessions import IssuedBuilderSession
from novy.domain.builder_sessions import ABSOLUTE_LIFETIME
from novy.infrastructure.csrf import (
    CSRF_COOKIE_NAME,
    PREAUTH_COOKIE_NAME,
    PREAUTH_TTL_SECONDS,
)

BUILDER_SESSION_COOKIE = "__Host-novy_builder_session"
CSRF_COOKIE_MAX_AGE = 2592000


def set_builder_session_cookie(response: Response, issued: IssuedBuilderSession) -> None:
    response.set_cookie(
        key=BUILDER_SESSION_COOKIE,
        value=issued.credential,
        max_age=int(ABSOLUTE_LIFETIME.total_seconds()),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


def clear_builder_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=BUILDER_SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


def set_csrf_cookie(response: Response, token: str) -> None:
    """Set the script-readable CSRF token; it is not an authentication secret."""
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        max_age=CSRF_COOKIE_MAX_AGE,
        path="/",
        secure=True,
        httponly=False,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


def set_preauth_cookie(response: Response, state: str) -> None:
    """Set the short-lived, opaque pre-authentication CSRF state."""
    response.set_cookie(
        key=PREAUTH_COOKIE_NAME,
        value=state,
        max_age=PREAUTH_TTL_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


def clear_auth_cookies(response: Response) -> None:
    """Delete every auth/CSRF cookie with attributes matching how it was set."""
    response.delete_cookie(
        key=BUILDER_SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=False,
        samesite="lax",
    )
    response.delete_cookie(
        key=PREAUTH_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
