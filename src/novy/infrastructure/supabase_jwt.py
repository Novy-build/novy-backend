"""Validation of Supabase-issued builder access tokens."""

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx2
import jwt

_MAX_TOKEN_CHARS = 16384
_MAX_CACHED_KEYS = 32


@dataclass(frozen=True, slots=True)
class VerifiedSupabaseIdentity:
    issuer: str
    subject: str
    email: str
    display_name: str
    session_id: str
    role: str


class SupabaseTokenError(Exception):
    """Fixed, caller-facing failure; never carries provider or decode details."""

    def __init__(self) -> None:
        super().__init__("Invalid Supabase token")


def _display_name(metadata: Any, email: str) -> str:
    if isinstance(metadata, dict):
        for key in ("display_name", "name", "full_name"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return email.split("@", 1)[0]


class SupabaseJwtValidator:
    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx2.AsyncClient,
        allowed_algorithms: tuple[str, ...] = ("ES256", "RS256"),
        leeway_seconds: int = 5,
        cache_ttl_seconds: int = 300,
    ) -> None:
        if not isinstance(base_url, str):
            raise ValueError("Invalid Supabase configuration")
        try:
            parsed = urlparse(base_url)
        except ValueError:
            raise ValueError("Invalid Supabase configuration") from None
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Invalid Supabase configuration")
        self._issuer = base_url.rstrip("/") + "/auth/v1"
        self._jwks_url = self._issuer + "/.well-known/jwks.json"
        self._http = http_client
        self._algorithms = tuple(allowed_algorithms)
        self._leeway = leeway_seconds
        self._cache_ttl = cache_ttl_seconds
        self._keys: dict[str, dict[str, Any]] = {}
        self._expires_at = 0.0

    async def _jwks(self, *, force: bool) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        if not force and self._keys and now < self._expires_at:
            return self._keys
        try:
            response = await self._http.get(self._jwks_url)
            if not 200 <= response.status_code < 300:
                raise ValueError
            payload = response.json()
        except Exception:
            raise SupabaseTokenError() from None
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise SupabaseTokenError() from None
        keys: dict[str, dict[str, Any]] = {}
        for item in payload["keys"]:
            if not isinstance(item, dict):
                raise SupabaseTokenError() from None
            kid = item.get("kid")
            if isinstance(kid, str) and kid:
                keys[kid] = item
            if len(keys) >= _MAX_CACHED_KEYS:
                break
        if not keys:
            raise SupabaseTokenError() from None
        self._keys = keys
        self._expires_at = time.monotonic() + self._cache_ttl
        return keys

    @staticmethod
    def _find_key(keys: dict[str, dict[str, Any]], kid: str, alg: str) -> dict[str, Any] | None:
        candidate = keys.get(kid)
        if candidate is not None and candidate.get("alg") == alg:
            return candidate
        return None

    async def validate(self, token: str) -> VerifiedSupabaseIdentity:
        if not isinstance(token, str) or not token.strip() or len(token) > _MAX_TOKEN_CHARS:
            raise SupabaseTokenError() from None
        try:
            header = jwt.get_unverified_header(token)
        except Exception:
            raise SupabaseTokenError() from None
        if not isinstance(header, dict):
            raise SupabaseTokenError() from None
        kid = header.get("kid")
        alg = header.get("alg")
        if (
            not isinstance(kid, str)
            or not kid
            or not isinstance(alg, str)
            or not alg
            or alg.startswith("HS")
            or alg not in self._algorithms
        ):
            raise SupabaseTokenError() from None

        jwk = self._find_key(await self._jwks(force=False), kid, alg)
        if jwk is None:
            jwk = self._find_key(await self._jwks(force=True), kid, alg)
        if jwk is None:
            raise SupabaseTokenError() from None

        try:
            key = jwt.PyJWK.from_dict(jwk, algorithm=alg).key
        except Exception:
            raise SupabaseTokenError() from None
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[alg],
                issuer=self._issuer,
                audience="authenticated",
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
                leeway=self._leeway,
            )
        except Exception:
            raise SupabaseTokenError() from None
        return self._identity(claims)

    def _identity(self, claims: dict[str, Any]) -> VerifiedSupabaseIdentity:
        issuer = claims.get("iss")
        subject = claims.get("sub")
        email = claims.get("email")
        session_id = claims.get("session_id")
        role = claims.get("role")
        if (
            not isinstance(issuer, str)
            or issuer != self._issuer
            or not isinstance(subject, str)
            or not subject.strip()
            or not isinstance(email, str)
            or not email.strip()
            or not isinstance(session_id, str)
            or not session_id.strip()
            or role != "authenticated"
            or claims.get("is_anonymous") is True
        ):
            raise SupabaseTokenError() from None
        return VerifiedSupabaseIdentity(
            issuer=issuer,
            subject=subject,
            email=email,
            display_name=_display_name(claims.get("user_metadata"), email),
            session_id=session_id,
            role=role,
        )
