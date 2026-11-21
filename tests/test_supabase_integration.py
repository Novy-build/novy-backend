"""Offline contract tests for the Supabase Auth and JWT adapters."""

import asyncio
import socket
import time
from typing import Any, NoReturn

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from novy.domain.builder_sessions import ProviderTokens
from novy.infrastructure.supabase_auth import (
    SupabaseAuthClient,
    SupabaseAuthError,
    SupabaseAuthResult,
)
from novy.infrastructure.supabase_jwt import (
    SupabaseJwtValidator,
    SupabaseTokenError,
    VerifiedSupabaseIdentity,
)

BASE_URL = "https://example.supabase.co"
PUBLISHABLE_KEY = "synthetic-publishable-key"
USER = {
    "id": "user-123",
    "email": "builder@example.com",
    "user_metadata": {"display_name": "Builder Name"},
}
TOKENS = {"access_token": "synthetic-access-token", "refresh_token": "synthetic-refresh-token"}
LEAK = "LEAKED_SUPER_SECRET_VALUE"


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("Unexpected network access")

    monkeypatch.setattr(socket, "getaddrinfo", reject)
    monkeypatch.setattr(socket, "create_connection", reject)


# ---------------------------------------------------------------------------
# SupabaseAuthClient
# ---------------------------------------------------------------------------


def auth_client(handler: Any) -> SupabaseAuthClient:
    transport = httpx2.MockTransport(handler)
    http = httpx2.AsyncClient(transport=transport)
    return SupabaseAuthClient(base_url=BASE_URL, publishable_key=PUBLISHABLE_KEY, http_client=http)


def test_signup_pending_confirmation_has_no_tokens() -> None:
    seen: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"user": USER})

    async def scenario() -> SupabaseAuthResult:
        return await auth_client(handler).sign_up("builder@example.com", "pw", "IGNORED_BODY_NAME")

    result = asyncio.run(scenario())
    assert result.user.id == "user-123"
    assert result.user.email == "builder@example.com"
    assert result.user.display_name == "Builder Name"
    assert result.tokens is None

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == BASE_URL + "/auth/v1/signup"
    assert request.headers["apikey"] == PUBLISHABLE_KEY
    assert request.headers["X-Supabase-Api-Version"] == "2024-01-01"
    assert request.headers["Content-Type"] == "application/json"
    assert "Authorization" not in request.headers
    assert __import__("json").loads(request.content) == {
        "email": "builder@example.com",
        "password": "pw",
        "data": {"display_name": "IGNORED_BODY_NAME"},
    }


@pytest.mark.parametrize(
    ("payload", "expected_name"),
    [
        (
            {
                "access_token": TOKENS["access_token"],
                "refresh_token": TOKENS["refresh_token"],
                "user": USER,
            },
            "Builder Name",
        ),
        (
            {
                "session": TOKENS,
                "user": {
                    "id": "user-123",
                    "email": "builder@example.com",
                    "user_metadata": {"name": "Metadata Name"},
                },
            },
            "Metadata Name",
        ),
        (
            {
                "session": TOKENS,
                "user": {"id": "user-123", "email": "builder@example.com"},
            },
            "builder",
        ),
    ],
)
def test_signup_with_session_returns_tokens_and_display_name_fallback(
    payload: dict[str, Any], expected_name: str
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    async def scenario() -> SupabaseAuthResult:
        return await auth_client(handler).sign_up("builder@example.com", "pw", "ignored")

    result = asyncio.run(scenario())
    assert result.tokens == ProviderTokens(
        access_token=TOKENS["access_token"], refresh_token=TOKENS["refresh_token"]
    )
    assert result.user.display_name == expected_name


@pytest.mark.parametrize(
    ("status", "body", "expected_code", "expected_status"),
    [
        (409, {"code": "email_exists"}, "email_already_registered", 409),
        (409, {"code": "user_already_exists"}, "email_already_registered", 409),
        (
            409,
            {"code": 409, "error_code": "email_exists", "message": LEAK},
            "email_already_registered",
            409,
        ),
    ],
)
def test_signup_duplicate_mapping_and_no_leak(
    status: int, body: dict[str, Any], expected_code: str, expected_status: int
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=body)

    async def scenario() -> None:
        await auth_client(handler).sign_up("builder@example.com", "pw", "ignored")

    with pytest.raises(SupabaseAuthError) as info:
        asyncio.run(scenario())
    assert info.value.code == expected_code
    assert info.value.status_code == expected_status
    assert LEAK not in str(info.value)
    assert LEAK not in repr(info.value)


@pytest.mark.parametrize(
    ("status", "body", "expected_code", "expected_status"),
    [
        (401, {"code": "invalid_credentials", "message": LEAK}, "invalid_credentials", 401),
        (400, {"error_code": "email_not_confirmed", "msg": LEAK}, "email_not_confirmed", 400),
    ],
)
def test_signin_error_mapping(
    status: int, body: dict[str, Any], expected_code: str, expected_status: int
) -> None:
    seen: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(status, json=body)

    async def scenario() -> None:
        await auth_client(handler).sign_in("builder@example.com", "pw")

    with pytest.raises(SupabaseAuthError) as info:
        asyncio.run(scenario())
    assert (info.value.code, info.value.status_code) == (expected_code, expected_status)
    request = seen[0]
    assert str(request.url) == BASE_URL + "/auth/v1/token?grant_type=password"
    assert __import__("json").loads(request.content) == {
        "email": "builder@example.com",
        "password": "pw",
    }
    assert LEAK not in str(info.value)


@pytest.mark.parametrize(
    ("code", "expected_code", "expected_status"),
    [
        ("refresh_token_not_found", "invalid_session", 401),
        ("refresh_token_already_used", "invalid_session", 401),
    ],
)
def test_refresh_rotation_errors(code: str, expected_code: str, expected_status: int) -> None:
    seen: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(401, json={"code": code, "message": LEAK})

    async def scenario() -> None:
        await auth_client(handler).refresh("old-refresh")

    with pytest.raises(SupabaseAuthError) as info:
        asyncio.run(scenario())
    assert (info.value.code, info.value.status_code) == (expected_code, expected_status)
    request = seen[0]
    assert str(request.url) == BASE_URL + "/auth/v1/token?grant_type=refresh_token"
    assert __import__("json").loads(request.content) == {"refresh_token": "old-refresh"}
    assert LEAK not in str(info.value)


def test_refresh_success() -> None:
    new_tokens = {"access_token": "new-access", "refresh_token": "new-refresh"}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"session": new_tokens, "user": USER})

    async def scenario() -> SupabaseAuthResult:
        return await auth_client(handler).refresh("old-refresh")

    result = asyncio.run(scenario())
    assert result.tokens == ProviderTokens("new-access", "new-refresh")
    assert result.user.id == "user-123"


@pytest.mark.parametrize(
    ("status", "body", "expected_code", "expected_status"),
    [
        (429, {"code": "rate_limit_exceeded"}, "rate_limited", 429),
        (429, {"code": "over_email_send_rate_limit"}, "rate_limited", 429),
        (409, {"code": "conflict"}, "auth_provider_conflict", 409),
        (503, {"code": "whatever"}, "auth_provider_error", 502),
        (418, {"code": "unknown"}, "auth_provider_error", 418),
    ],
)
def test_other_error_mappings(
    status: int, body: dict[str, Any], expected_code: str, expected_status: int
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=body)

    async def scenario() -> None:
        await auth_client(handler).sign_in("builder@example.com", "pw")

    with pytest.raises(SupabaseAuthError) as info:
        asyncio.run(scenario())
    assert (info.value.code, info.value.status_code) == (expected_code, expected_status)


@pytest.mark.parametrize(
    "payload",
    [
        {"user": {"id": 123, "email": "builder@example.com"}},
        {"user": {"id": "user-123", "email": None}},
        {"user": "not-a-dict"},
        {"user": {"id": 123, "email": "a@b.co"}, "message": LEAK},
        {"session": {"access_token": "only-access"}, "user": USER},
    ],
)
def test_malformed_success_response_fails_closed(payload: dict[str, Any]) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    async def scenario() -> None:
        await auth_client(handler).sign_in("builder@example.com", "pw")

    with pytest.raises(SupabaseAuthError) as info:
        asyncio.run(scenario())
    assert info.value.code == "auth_provider_error"
    assert info.value.status_code == 502
    assert LEAK not in str(info.value)


def test_non_json_success_and_network_failure_are_generic() -> None:
    async def bad_json(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"not json " + LEAK.encode())

    async def network_error(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(LEAK)

    async def scenario(handler: Any) -> None:
        await auth_client(handler).sign_in("builder@example.com", "pw")

    for handler in (bad_json, network_error):
        with pytest.raises(SupabaseAuthError) as info:
            asyncio.run(scenario(handler))
        assert info.value.code == "auth_provider_error"
        assert LEAK not in str(info.value)
        assert LEAK not in repr(info.value)


@pytest.mark.parametrize(
    ("base_url", "key"),
    [
        ("http://example.supabase.co", PUBLISHABLE_KEY),
        ("not-a-url", PUBLISHABLE_KEY),
        ("https://", PUBLISHABLE_KEY),
        ("", PUBLISHABLE_KEY),
        (BASE_URL, ""),
        (BASE_URL, "   "),
    ],
)
def test_constructor_rejects_invalid_configuration(base_url: str, key: str) -> None:
    http = httpx2.AsyncClient()
    with pytest.raises(ValueError) as info:
        SupabaseAuthClient(base_url=base_url, publishable_key=key, http_client=http)
    assert str(info.value) == "Invalid Supabase configuration"
    assert PUBLISHABLE_KEY not in str(info.value)
    assert key not in str(info.value) or not key.strip()


# ---------------------------------------------------------------------------
# SupabaseJwtValidator
# ---------------------------------------------------------------------------

ISSUER = BASE_URL + "/auth/v1"
JWKS_PATH = "/auth/v1/.well-known/jwks.json"
KID = "synthetic-kid-1"
_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_PUBLIC_JWK = jwt.algorithms.ECAlgorithm.to_jwk(_PRIVATE_KEY.public_key(), as_dict=True)
_PUBLIC_JWK.update({"kid": KID, "alg": "ES256", "use": "sig"})


def make_token(
    *,
    issuer: str = ISSUER,
    audience: str = "authenticated",
    age_seconds: int = 0,
    expires_in: int = 300,
    kid: str = KID,
    alg: str = "ES256",
    extra: dict[str, Any] | None = None,
) -> str:
    now = int(time.time()) - age_seconds
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": "user-123",
        "iat": now,
        "exp": now + expires_in,
        "role": "authenticated",
        "session_id": "session-abc",
        "email": "builder@example.com",
        "user_metadata": {"display_name": "Builder Name"},
    }
    if extra:
        claims.update(extra)
    return jwt.encode(claims, _PRIVATE_KEY, algorithm=alg, headers={"kid": kid, "alg": alg})


def jwt_validator(
    responses: list[httpx2.Response],
    calls: list[str],
    *,
    cache_ttl_seconds: int = 300,
) -> SupabaseJwtValidator:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        assert request.url.path == JWKS_PATH
        if not responses:
            raise AssertionError("Unexpected JWKS fetch")
        return responses.pop(0)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return SupabaseJwtValidator(
        base_url=BASE_URL, http_client=http, cache_ttl_seconds=cache_ttl_seconds
    )


def jwks_response(*keys: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(200, json={"keys": list(keys)})


def test_valid_token_returns_identity() -> None:
    calls: list[str] = []
    validator = jwt_validator([jwks_response(_PUBLIC_JWK)], calls)
    identity = asyncio.run(validator.validate(make_token()))
    assert identity == VerifiedSupabaseIdentity(
        issuer=ISSUER,
        subject="user-123",
        email="builder@example.com",
        display_name="Builder Name",
        session_id="session-abc",
        role="authenticated",
    )
    assert calls == [JWKS_PATH]


@pytest.mark.parametrize("field", ["display_name", "name", "full_name"])
def test_display_name_fallbacks(field: str) -> None:
    calls: list[str] = []
    validator = jwt_validator([jwks_response(_PUBLIC_JWK)], calls)
    token = make_token(extra={"user_metadata": {field: "From Metadata"}})
    assert asyncio.run(validator.validate(token)).display_name == "From Metadata"


def test_display_name_falls_back_to_email_local_part() -> None:
    calls: list[str] = []
    validator = jwt_validator([jwks_response(_PUBLIC_JWK)], calls)
    token = make_token(extra={"user_metadata": {}, "email": "local.part@example.com"})
    assert asyncio.run(validator.validate(token)).display_name == "local.part"


@pytest.mark.parametrize(
    "token",
    [
        make_token(issuer="https://evil.example/auth/v1"),
        make_token(audience="not-authenticated"),
        make_token(age_seconds=600),
    ],
)
def test_invalid_claims_are_rejected(token: str) -> None:
    calls: list[str] = []
    validator = jwt_validator([jwks_response(_PUBLIC_JWK)], calls)
    with pytest.raises(SupabaseTokenError) as info:
        asyncio.run(validator.validate(token))
    assert str(info.value) == "Invalid Supabase token"
    assert info.value.__cause__ is None


@pytest.mark.parametrize(
    "extra",
    [
        {"is_anonymous": True},
        {"role": "anon"},
        {"role": "service_role"},
        {"role": None},
        {"session_id": ""},
        {"session_id": "   "},
        {"email": ""},
        {"email": None},
        {"sub": ""},
    ],
)
def test_session_and_role_guards(extra: dict[str, Any]) -> None:
    calls: list[str] = []
    validator = jwt_validator([jwks_response(_PUBLIC_JWK)], calls)
    with pytest.raises(SupabaseTokenError):
        asyncio.run(validator.validate(make_token(extra=extra)))


def test_missing_required_claim_is_rejected() -> None:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": "authenticated",
        "sub": "user-123",
        "iat": now,
        "exp": now + 300,
        "role": "authenticated",
        "session_id": "s",
        "email": "a@b.co",
    }
    claims.pop("aud")
    token = jwt.encode(
        claims, _PRIVATE_KEY, algorithm="ES256", headers={"kid": KID, "alg": "ES256"}
    )
    calls: list[str] = []
    validator = jwt_validator([jwks_response(_PUBLIC_JWK)], calls)
    with pytest.raises(SupabaseTokenError):
        asyncio.run(validator.validate(token))


def test_hs256_rejected_before_network() -> None:
    calls: list[str] = []
    validator = jwt_validator([], calls)
    token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": "authenticated",
            "sub": "s",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        "symmetric-secret-that-is-long-enough-for-hs256-validation",
        algorithm="HS256",
        headers={"kid": KID, "alg": "HS256"},
    )
    with pytest.raises(SupabaseTokenError):
        asyncio.run(validator.validate(token))
    assert calls == []


@pytest.mark.parametrize("token", [123, "", "   ", "a" * 20000])
def test_bad_token_shape_rejected_before_network(token: Any) -> None:
    calls: list[str] = []
    validator = jwt_validator([], calls)
    with pytest.raises(SupabaseTokenError):
        asyncio.run(validator.validate(token))
    assert calls == []


def test_wrong_kid_forces_refresh_then_fails() -> None:
    calls: list[str] = []
    validator = jwt_validator([jwks_response(_PUBLIC_JWK), jwks_response(_PUBLIC_JWK)], calls)
    token = make_token(kid="unknown-kid")
    with pytest.raises(SupabaseTokenError):
        asyncio.run(validator.validate(token))
    assert calls == [JWKS_PATH, JWKS_PATH]


def test_unknown_kid_refresh_finds_rotated_key() -> None:
    rotated = dict(_PUBLIC_JWK)
    rotated["kid"] = "rotated-kid"
    calls: list[str] = []
    validator = jwt_validator([jwks_response(_PUBLIC_JWK), jwks_response(rotated)], calls)
    token = make_token(kid="rotated-kid")
    assert asyncio.run(validator.validate(token)).subject == "user-123"
    assert calls == [JWKS_PATH, JWKS_PATH]


def test_jwks_key_must_match_alg_as_well_as_kid() -> None:
    wrong_alg = dict(_PUBLIC_JWK)
    wrong_alg["alg"] = "RS256"
    calls: list[str] = []
    validator = jwt_validator([jwks_response(wrong_alg), jwks_response(wrong_alg)], calls)
    with pytest.raises(SupabaseTokenError):
        asyncio.run(validator.validate(make_token()))
    assert calls == [JWKS_PATH, JWKS_PATH]


def test_jwks_cache_avoids_refetch() -> None:
    calls: list[str] = []
    validator = jwt_validator([jwks_response(_PUBLIC_JWK)], calls)
    assert asyncio.run(validator.validate(make_token())).subject == "user-123"
    assert asyncio.run(validator.validate(make_token(extra={"email": "other@b.co"}))).email == (
        "other@b.co"
    )
    assert calls == [JWKS_PATH]


def test_cache_ttl_zero_forces_refetch() -> None:
    calls: list[str] = []
    validator = jwt_validator(
        [jwks_response(_PUBLIC_JWK), jwks_response(_PUBLIC_JWK)], calls, cache_ttl_seconds=0
    )
    asyncio.run(validator.validate(make_token()))
    asyncio.run(validator.validate(make_token()))
    assert calls == [JWKS_PATH, JWKS_PATH]


def test_jwks_fetch_failure_is_not_cached() -> None:
    calls: list[str] = []
    validator = jwt_validator(
        [httpx2.Response(503, json={"error": "down"}), jwks_response(_PUBLIC_JWK)], calls
    )
    with pytest.raises(SupabaseTokenError):
        asyncio.run(validator.validate(make_token()))
    assert asyncio.run(validator.validate(make_token())).subject == "user-123"
    assert calls == [JWKS_PATH, JWKS_PATH]


@pytest.mark.parametrize(
    "body",
    [{"keys": "not-a-list"}, {"keys": []}, {"unexpected": True}, {"keys": [123]}],
)
def test_malformed_jwks_fails_closed(body: dict[str, Any]) -> None:
    calls: list[str] = []
    validator = jwt_validator([httpx2.Response(200, json=body)], calls)
    with pytest.raises(SupabaseTokenError):
        asyncio.run(validator.validate(make_token()))
    assert calls == [JWKS_PATH]


def test_constructor_rejects_insecure_url() -> None:
    http = httpx2.AsyncClient()
    with pytest.raises(ValueError):
        SupabaseJwtValidator(base_url="http://example.supabase.co", http_client=http)
