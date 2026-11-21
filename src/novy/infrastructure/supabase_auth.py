"""Supabase Auth adapter for builder credentials.

Supabase owns builder credentials. This adapter only translates HTTP responses
into domain-neutral results and never persists provider secrets itself.
"""

from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlparse

import httpx2

from novy.domain.builder_sessions import ProviderTokens

_API_VERSION = "2024-01-01"

# Public error codes mapped from provider codes. The external message is fixed;
# the raw provider body (which may contain tokens or other secrets) is discarded.
_ERROR_MAP: dict[str, tuple[str, int]] = {
    "email_exists": ("email_already_registered", 409),
    "user_already_exists": ("email_already_registered", 409),
    "email_not_confirmed": ("email_not_confirmed", 400),
    "invalid_credentials": ("invalid_credentials", 401),
    "refresh_token_not_found": ("invalid_session", 401),
    "refresh_token_already_used": ("invalid_session", 401),
    "conflict": ("auth_provider_conflict", 409),
}


@dataclass(frozen=True, slots=True)
class SupabaseUser:
    id: str
    email: str
    display_name: str


@dataclass(frozen=True, slots=True)
class SupabaseAuthResult:
    user: SupabaseUser
    tokens: ProviderTokens | None


class SupabaseAuthError(Exception):
    """Safe, caller-facing authentication failure.

    The provider response body is never embedded here: it can contain tokens,
    keys, or other sensitive data.
    """

    def __init__(self, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__("Supabase authentication failed")


def _display_name(user_data: dict[str, Any], email: str) -> str:
    metadata = user_data.get("user_metadata")
    if isinstance(metadata, dict):
        for key in ("display_name", "name"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return email.split("@", 1)[0]


def _parse_success(payload: Any) -> SupabaseAuthResult:
    if not isinstance(payload, dict):
        raise ValueError
    user_data: Any = payload.get("user", payload)
    if not isinstance(user_data, dict):
        raise ValueError
    user_id = user_data.get("id")
    email = user_data.get("email")
    if (
        not isinstance(user_id, str)
        or not user_id.strip()
        or not isinstance(email, str)
        or not email.strip()
    ):
        raise ValueError

    tokens: ProviderTokens | None = None
    if "session" in payload:
        session_data = payload["session"]
        if not isinstance(session_data, dict):
            raise ValueError
        # ProviderTokens validates the values at runtime; the cast only
        # satisfies static typing for the raw JSON fields.
        tokens = ProviderTokens(
            access_token=cast(str, session_data.get("access_token")),
            refresh_token=cast(str, session_data.get("refresh_token")),
        )
    else:
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        if access_token is not None or refresh_token is not None:
            tokens = ProviderTokens(
                access_token=cast(str, access_token),
                refresh_token=cast(str, refresh_token),
            )

    user = SupabaseUser(user_id, email, _display_name(user_data, email))
    return SupabaseAuthResult(user=user, tokens=tokens)


def _provider_code(response: httpx2.Response) -> str:
    """Extract a provider code without ever using the raw message/msg body."""
    try:
        body = response.json()
    except Exception:
        return ""
    if not isinstance(body, dict):
        return ""
    # Newer Supabase responses use a string ``code``; legacy responses use an
    # integer ``code`` plus a separate ``error_code`` string.
    code = body.get("code")
    if isinstance(code, str) and code.strip():
        return code.strip().lower()
    error_code = body.get("error_code")
    if isinstance(error_code, str) and error_code.strip():
        return error_code.strip().lower()
    return ""


def _provider_error(response: httpx2.Response) -> SupabaseAuthError:
    status = response.status_code
    code = _provider_code(response)
    mapped = _ERROR_MAP.get(code)
    if mapped is not None:
        return SupabaseAuthError(mapped[0], mapped[1])
    if "rate_limit" in code:
        return SupabaseAuthError("rate_limited", 429)
    return SupabaseAuthError("auth_provider_error", 502 if status >= 500 else status)


class SupabaseAuthClient:
    def __init__(
        self,
        *,
        base_url: str,
        publishable_key: str,
        http_client: httpx2.AsyncClient,
        timeout: float = 5.0,
    ) -> None:
        if (
            not isinstance(base_url, str)
            or not isinstance(publishable_key, str)
            or not publishable_key.strip()
        ):
            raise ValueError("Invalid Supabase configuration")
        try:
            parsed = urlparse(base_url)
        except ValueError:
            raise ValueError("Invalid Supabase configuration") from None
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Invalid Supabase configuration")
        self._base_url = base_url.rstrip("/")
        self._publishable_key = publishable_key
        self._http = http_client
        self._timeout = timeout

    async def _post(self, path: str, body: dict[str, Any]) -> httpx2.Response:
        try:
            return await self._http.post(
                self._base_url + path,
                headers={
                    "apikey": self._publishable_key,
                    "X-Supabase-Api-Version": _API_VERSION,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self._timeout,
            )
        except httpx2.HTTPError:
            raise SupabaseAuthError("auth_provider_error", 502) from None

    def _result(self, response: httpx2.Response) -> SupabaseAuthResult:
        if not 200 <= response.status_code < 300:
            raise _provider_error(response)
        try:
            return _parse_success(response.json())
        except (ValueError, TypeError, KeyError, AttributeError):
            raise SupabaseAuthError("auth_provider_error", 502) from None

    async def sign_up(self, email: str, password: str, display_name: str) -> SupabaseAuthResult:
        """Register a builder.

        A 2xx response without a session means confirmation is pending. The
        returned user id is not proof of a real account and must not be used to
        create a profile until a confirmed session exists.
        """
        response = await self._post(
            "/auth/v1/signup",
            {"email": email, "password": password, "data": {"display_name": display_name}},
        )
        return self._result(response)

    async def sign_in(self, email: str, password: str) -> SupabaseAuthResult:
        response = await self._post(
            "/auth/v1/token?grant_type=password",
            {"email": email, "password": password},
        )
        return self._result(response)

    async def refresh(self, refresh_token: str) -> SupabaseAuthResult:
        response = await self._post(
            "/auth/v1/token?grant_type=refresh_token",
            {"refresh_token": refresh_token},
        )
        return self._result(response)
