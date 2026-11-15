"""Cookie transport only; callers must authenticate and enforce CSRF and origin checks.

Set the cookie on initial issuance, not automatically on every request. Server-side
absolute expiry, idle expiry, and revocation remain authoritative.
"""

from fastapi import Response

from novy.application.builder_sessions import IssuedBuilderSession
from novy.domain.builder_sessions import ABSOLUTE_LIFETIME

BUILDER_SESSION_COOKIE = "__Host-novy_builder_session"


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
