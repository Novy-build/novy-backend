import asyncio
import base64
import functools
import json
import os
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from http.cookies import SimpleCookie
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from novy.infrastructure import csrf as csrf_module
from novy.infrastructure.csrf import (
    CSRF_COOKIE_NAME,
    PREAUTH_COOKIE_NAME,
    PREAUTH_TTL_SECONDS,
    CsrfService,
    is_canonical_token,
)
from novy.presentation.http.auth_dependencies import (
    allowed_origins,
    get_session_credential,
    require_session_service,
    require_unsafe_request_guards,
)
from novy.presentation.http.builder_session_cookie import (
    BUILDER_SESSION_COOKIE,
    CSRF_COOKIE_MAX_AGE,
    clear_auth_cookies,
    set_csrf_cookie,
    set_preauth_cookie,
)
from novy.presentation.http.errors import (
    ApiError,
    api_error,
    api_error_handler,
    http_exception_handler,
    install_error_handlers,
    unhandled_error_handler,
    validation_error_handler,
)

SECRET = "synthetic-secret-value"
CREDENTIAL = "A" * 43
ORIGIN = "https://app.example.test"


def _sync[T](fn: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., T]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


@pytest.fixture
def service() -> CsrfService:
    return CsrfService(lookup_key=os.urandom(32), environment="test")


@dataclass(frozen=True)
class _Settings:
    environment: str
    allowed_origins: str


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# --- CsrfService -----------------------------------------------------------


def test_constructor_validation_and_repr() -> None:
    key = os.urandom(32)
    assert repr(CsrfService(lookup_key=key, environment="production")) == "CsrfService(<redacted>)"
    for invalid_key in (b"", b"x" * 31, b"x" * 33, "x" * 32, None, bytearray(32)):
        with pytest.raises(ValueError, match="Invalid CSRF service configuration"):
            CsrfService(lookup_key=cast(bytes, invalid_key), environment="test")
    for invalid_env in ("", "TEST", "staging", None, 1):
        with pytest.raises(ValueError):
            CsrfService(lookup_key=key, environment=cast(str, invalid_env))
    for env in ("development", "test", "production"):
        assert CsrfService(lookup_key=key, environment=env)


def test_preauth_issue_and_verify(service: CsrfService) -> None:
    first = service.issue_preauth()
    second = service.issue_preauth()
    assert first.state != second.state
    assert first.token != second.token
    nonce_part, expiry_part, signature_part = first.state.split(".")
    assert len(csrf_module._b64url_decode(nonce_part)) == 32
    assert len(csrf_module._b64url_decode(signature_part)) == 32
    assert all(is_canonical_token(part) for part in (nonce_part, expiry_part, signature_part))
    assert is_canonical_token(first.token)
    assert service.verify_preauth(first.state, first.token)
    wrong_key = CsrfService(lookup_key=os.urandom(32), environment="test")
    assert not wrong_key.verify_preauth(first.state, first.token)


def test_preauth_expiry_bounds(monkeypatch: pytest.MonkeyPatch, service: CsrfService) -> None:
    now = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: float(now))
    preauth = service.issue_preauth()
    assert service.verify_preauth(preauth.state, preauth.token)

    monkeypatch.setattr(time, "time", lambda: float(now + 1))
    assert service.verify_preauth(preauth.state, preauth.token)

    monkeypatch.setattr(time, "time", lambda: float(now + PREAUTH_TTL_SECONDS))
    assert not service.verify_preauth(preauth.state, preauth.token)

    monkeypatch.setattr(time, "time", lambda: float(now - 61))
    assert not service.verify_preauth(preauth.state, preauth.token)

    monkeypatch.setattr(time, "time", lambda: float(now - 59))
    assert service.verify_preauth(preauth.state, preauth.token)


def test_preauth_tamper_and_canonical_encoding(service: CsrfService) -> None:
    preauth = service.issue_preauth()
    nonce_part, expiry_part, signature_part = preauth.state.split(".")

    tampered_signature = ("B" if signature_part[0] != "B" else "C") + signature_part[1:]
    tampered_token = ("B" if preauth.token[0] != "B" else "C") + preauth.token[1:]
    for state, token in (
        (preauth.state + ".", preauth.token),
        (".".join((nonce_part, expiry_part)), preauth.token),
        (".".join((nonce_part + "=", expiry_part, signature_part)), preauth.token),
        (".".join((nonce_part, expiry_part, tampered_signature)), preauth.token),
        (preauth.state, preauth.token + "="),
        (preauth.state, tampered_token),
        (preauth.state, SECRET),
        (cast(str, None), preauth.token),
        (preauth.state, cast(str, b"bytes")),
    ):
        assert not service.verify_preauth(state, token)

    # A non-canonical expiry (leading zero) is rejected even if it were signed.
    nonce = b"\x07" * 32
    noncanonical_expiry = _b64url(b"0900")
    signature = _b64url(service._sign(csrf_module._PREAUTH_STATE_DOMAIN, nonce + b"0900"))
    crafted_state = ".".join((_b64url(nonce), noncanonical_expiry, signature))
    crafted_token = _b64url(
        service._sign(csrf_module._PREAUTH_TOKEN_DOMAIN, crafted_state.encode("ascii"))
    )
    assert not service.verify_preauth(crafted_state, crafted_token)


def test_session_token_binding(service: CsrfService) -> None:
    token = service.token_for_session(CREDENTIAL)
    assert is_canonical_token(token)
    assert service.verify_session(CREDENTIAL, token)
    assert service.verify_session(CREDENTIAL, token) == service.verify_session(CREDENTIAL, token)

    other_credential = _b64url(os.urandom(32))
    assert token != service.token_for_session(other_credential)
    assert not service.verify_session(other_credential, token)
    assert not service.verify_session(CREDENTIAL, service.token_for_session(other_credential))

    tampered = ("B" if token[0] != "B" else "C") + token[1:]
    for credential, candidate in (
        (CREDENTIAL, tampered),
        (CREDENTIAL, token + "="),
        (CREDENTIAL, SECRET),
        ("short", token),
        (cast(str, None), token),
        (CREDENTIAL, cast(str, None)),
    ):
        assert not service.verify_session(credential, candidate)
    with pytest.raises(ValueError, match="Invalid session credential"):
        service.token_for_session("short")


def test_is_canonical_token() -> None:
    for value in ("A" * 43, "YQ", "AQ"):
        assert is_canonical_token(value)
    for value in ("", "A=", "A==", "YQ==", " YQ", "YQ\n", "a+b", "a/b", SECRET):
        assert not is_canonical_token(value)
    assert not is_canonical_token(cast(str, 123))


# --- Origin parsing --------------------------------------------------------


def test_allowed_origins_defaults_and_configuration() -> None:
    assert allowed_origins(_Settings("development", "")) == (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    assert allowed_origins(_Settings("test", "  ")) == (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    assert allowed_origins(_Settings("production", "")) == ()
    configured = _Settings(
        "production",
        f" {ORIGIN}/ , https://second.example.test/,{ORIGIN}, ,",
    )
    assert allowed_origins(configured) == (ORIGIN, "https://second.example.test")


# --- Request guards --------------------------------------------------------


def _request(
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    settings: _Settings | None = None,
    csrf_service: CsrfService | None = None,
    session_service: object | None = None,
) -> Request:
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    if cookies:
        cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        raw_headers.append((b"cookie", cookie_header.encode("latin-1")))
    state_values: dict[str, Any] = {}
    if settings is not None:
        state_values["settings"] = settings
    if csrf_service is not None:
        state_values["csrf_service"] = csrf_service
    if session_service is not None:
        state_values["builder_session_service"] = session_service
    app = SimpleNamespace(state=SimpleNamespace(**state_values))
    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "server": ("example.test", 443),
        "client": ("127.0.0.1", 12345),
        "app": app,
    }
    return Request(cast(Scope, scope))


def _guarded_request(
    service: CsrfService,
    *,
    method: str = "POST",
    origin: str | None = ORIGIN,
    token: str | None = None,
    cookie_token: str | None = None,
    preauth_state: str | None = None,
    credential: str | None = None,
    settings: _Settings | None = None,
) -> Request:
    headers: dict[str, str] = {}
    if origin is not None:
        headers["Origin"] = origin
    if token is not None:
        headers["X-CSRF-Token"] = token
    cookies: dict[str, str] = {}
    if cookie_token is not None:
        cookies[CSRF_COOKIE_NAME] = cookie_token
    if preauth_state is not None:
        cookies[PREAUTH_COOKIE_NAME] = preauth_state
    if credential is not None:
        cookies[BUILDER_SESSION_COOKIE] = credential
    return _request(
        method=method,
        headers=headers,
        cookies=cookies,
        settings=settings if settings is not None else _Settings("production", ORIGIN),
        csrf_service=service,
    )


@_sync
async def test_unsafe_guards_skip_safe_methods(service: CsrfService) -> None:
    for method in ("GET", "HEAD", "OPTIONS"):
        await require_unsafe_request_guards(_request(method=method))


@_sync
async def test_unsafe_guards_allow_preauth_and_session(service: CsrfService) -> None:
    preauth = service.issue_preauth()
    await require_unsafe_request_guards(
        _guarded_request(
            service,
            token=preauth.token,
            cookie_token=preauth.token,
            preauth_state=preauth.state,
        )
    )

    session_token = service.token_for_session(CREDENTIAL)
    await require_unsafe_request_guards(
        _guarded_request(
            service,
            token=session_token,
            cookie_token=session_token,
            credential=CREDENTIAL,
        )
    )

    # Trailing slashes on the Origin header are normalized away.
    await require_unsafe_request_guards(
        _guarded_request(
            service,
            origin=f"{ORIGIN}/",
            token=session_token,
            cookie_token=session_token,
            credential=CREDENTIAL,
        )
    )


@_sync
async def test_unsafe_guards_fall_back_to_session_for_bad_preauth(
    service: CsrfService,
) -> None:
    session_token = service.token_for_session(CREDENTIAL)
    await require_unsafe_request_guards(
        _guarded_request(
            service,
            token=session_token,
            cookie_token=session_token,
            preauth_state="not-a-valid-state",
            credential=CREDENTIAL,
        )
    )


@pytest.mark.parametrize(
    ("origin", "expected"),
    [(None, "origin_not_allowed"), ("https://evil.example.test", "origin_not_allowed")],
)
@_sync
async def test_unsafe_guards_reject_bad_origin(
    service: CsrfService, origin: str | None, expected: str
) -> None:
    preauth = service.issue_preauth()
    with pytest.raises(ApiError) as error:
        await require_unsafe_request_guards(
            _guarded_request(
                service,
                origin=origin,
                token=preauth.token,
                cookie_token=preauth.token,
                preauth_state=preauth.state,
            )
        )
    assert (error.value.status_code, error.value.code) == (403, expected)
    assert ORIGIN not in error.value.message


@_sync
async def test_unsafe_guards_reject_bad_csrf_material(service: CsrfService) -> None:
    preauth = service.issue_preauth()
    cases: list[dict[str, Any]] = [
        {"token": None, "cookie_token": preauth.token, "preauth_state": preauth.state},
        {"token": preauth.token, "cookie_token": None, "preauth_state": preauth.state},
        {"token": preauth.token, "cookie_token": SECRET, "preauth_state": preauth.state},
        {"token": SECRET, "cookie_token": SECRET, "preauth_state": preauth.state},
        {
            "token": preauth.token + "=",
            "cookie_token": preauth.token,
            "preauth_state": preauth.state,
        },
        {"token": preauth.token, "cookie_token": preauth.token},
        {"token": preauth.token, "cookie_token": preauth.token, "credential": CREDENTIAL},
    ]
    for case in cases:
        with pytest.raises(ApiError) as error:
            await require_unsafe_request_guards(_guarded_request(service, **case))
        assert (error.value.status_code, error.value.code) == (403, "csrf_failed")
        assert preauth.token not in error.value.message


def test_session_credential_and_service_dependencies(service: CsrfService) -> None:
    assert (
        get_session_credential(_request(cookies={BUILDER_SESSION_COOKIE: CREDENTIAL})) == CREDENTIAL
    )
    for invalid in ("short", SECRET, None):
        cookies = {BUILDER_SESSION_COOKIE: invalid} if invalid is not None else {}
        with pytest.raises(ApiError) as error:
            get_session_credential(_request(cookies=cookies))
        assert (error.value.status_code, error.value.code) == (401, "session_required")
        if invalid is not None:
            assert invalid not in error.value.message

    sentinel = object()
    assert require_session_service(_request(session_service=sentinel)) is sentinel
    with pytest.raises(ApiError) as error:
        require_session_service(_request())
    assert (error.value.status_code, error.value.code) == (503, "auth_unavailable")


# --- Error envelope --------------------------------------------------------


def _body(response: Response) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(cast(bytes, response.body)))


@_sync
async def test_api_error_envelope() -> None:
    error = api_error(403, "origin_not_allowed", "Origin not allowed")
    assert isinstance(error, ApiError)
    response = await api_error_handler(_request(), error)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    body = _body(response)
    assert set(body) == {"error"}
    envelope = body["error"]
    assert set(envelope) == {"code", "message", "requestId"}
    assert envelope["code"] == "origin_not_allowed"
    assert envelope["message"] == "Origin not allowed"
    assert len(envelope["requestId"]) == 32
    int(cast(str, envelope["requestId"]), 16)

    with_details = await api_error_handler(
        _request(), ApiError(409, "stale_revision", "Conflict", details={"expected": 3})
    )
    assert _body(with_details)["error"]["details"] == {"expected": 3}


@_sync
async def test_validation_error_envelope_has_no_input_values() -> None:
    class FakeValidationError(Exception):
        def errors(self) -> list[dict[str, object]]:
            return [
                {
                    "loc": ("body", "secret_field"),
                    "msg": "Field required",
                    "input": SECRET,
                    "ctx": {"error": ValueError(SECRET)},
                }
            ]

    response = await validation_error_handler(_request(), FakeValidationError())
    assert response.status_code == 400
    body = _body(response)
    assert body["error"]["code"] == "validation_error"
    issues = body["error"]["details"]["issues"]
    assert len(issues) == 1
    assert set(issues[0]) == {"field", "location", "message"}
    assert issues[0]["field"] == "secret_field"
    assert issues[0]["location"] == ["body", "secret_field"]
    assert SECRET not in json.dumps(body)

    empty = await validation_error_handler(_request(), RequestValidationError([]))
    assert _body(empty)["error"]["details"]["issues"] == [
        {"field": "", "location": [], "message": "Invalid request"}
    ]


@_sync
async def test_http_and_unhandled_error_envelopes_hide_details() -> None:
    not_found = await http_exception_handler(
        _request(), StarletteHTTPException(status_code=404, detail=SECRET)
    )
    assert not_found.status_code == 404
    assert _body(not_found)["error"]["code"] == "not_found"
    assert SECRET not in json.dumps(_body(not_found))

    method = await http_exception_handler(
        _request(), StarletteHTTPException(status_code=405, detail=SECRET)
    )
    assert _body(method)["error"]["code"] == "method_not_allowed"

    other = await http_exception_handler(
        _request(), StarletteHTTPException(status_code=418, detail=SECRET)
    )
    assert _body(other)["error"]["code"] == "http_error"

    unhandled = await unhandled_error_handler(_request(), RuntimeError(SECRET))
    assert unhandled.status_code == 500
    assert _body(unhandled)["error"]["code"] == "internal_error"
    assert SECRET not in json.dumps(_body(unhandled))
    assert unhandled.headers["cache-control"] == "no-store"


def test_install_error_handlers() -> None:
    app = FastAPI()
    install_error_handlers(app)
    for kind in (
        ApiError,
        RequestValidationError,
        StarletteHTTPException,
        Exception,
    ):
        assert kind in app.exception_handlers


# --- Cookie transport ------------------------------------------------------


def test_set_csrf_and_preauth_cookie_attributes() -> None:
    token = "A" * 43
    response = Response()
    set_csrf_cookie(response, token)
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    assert list(cookie) == [CSRF_COOKIE_NAME]
    morsel = cookie[CSRF_COOKIE_NAME]
    assert morsel.value == token
    assert morsel["path"] == "/"
    assert morsel["domain"] == ""
    assert morsel["secure"] is True
    assert not morsel["httponly"]
    assert morsel["samesite"] == "lax"
    assert morsel["max-age"] == str(CSRF_COOKIE_MAX_AGE)
    assert response.headers["cache-control"] == "no-store"

    response = Response()
    set_preauth_cookie(response, "state-value")
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    assert list(cookie) == [PREAUTH_COOKIE_NAME]
    morsel = cookie[PREAUTH_COOKIE_NAME]
    assert morsel.value == "state-value"
    assert morsel["max-age"] == str(PREAUTH_TTL_SECONDS)
    assert morsel["secure"] is True
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "lax"
    assert morsel["path"] == "/"


def test_clear_auth_cookies_matches_set_attributes() -> None:
    response = Response()
    clear_auth_cookies(response)
    headers = response.headers.getlist("set-cookie")
    assert len(headers) == 3
    cookies: dict[str, Any] = {}
    for header in headers:
        cookie = SimpleCookie()
        cookie.load(header)
        assert len(cookie) == 1
        name = next(iter(cookie))
        cookies[name] = cookie[name]
    assert set(cookies) == {BUILDER_SESSION_COOKIE, CSRF_COOKIE_NAME, PREAUTH_COOKIE_NAME}
    expected_httponly = {
        BUILDER_SESSION_COOKIE: True,
        CSRF_COOKIE_NAME: False,
        PREAUTH_COOKIE_NAME: True,
    }
    for name, morsel in cookies.items():
        assert morsel.value == ""
        assert morsel["path"] == "/"
        assert morsel["domain"] == ""
        assert morsel["secure"] is True
        assert morsel["samesite"] == "lax"
        assert morsel["max-age"] == "0"
        assert bool(morsel["httponly"]) is expected_httponly[name]
    assert response.headers["cache-control"] == "no-store"


def test_builder_session_cookie_helpers_are_preserved() -> None:
    from novy.application.builder_sessions import IssuedBuilderSession
    from novy.presentation.http.builder_session_cookie import (
        clear_builder_session_cookie,
        set_builder_session_cookie,
    )

    response = Response()
    set_builder_session_cookie(response, IssuedBuilderSession(CREDENTIAL, "synthetic-record"))
    assert BUILDER_SESSION_COOKIE in response.headers["set-cookie"]
    response = Response()
    clear_builder_session_cookie(response)
    assert BUILDER_SESSION_COOKIE in response.headers["set-cookie"]
