from email.message import Message
from http.cookiejar import CookieJar
from http.cookies import SimpleCookie
from urllib.request import Request

from fastapi import FastAPI, Response

from novy.application.builder_sessions import IssuedBuilderSession
from novy.presentation.http import builder_session_cookie
from novy.presentation.http.builder_session_cookie import (
    BUILDER_SESSION_COOKIE,
    clear_builder_session_cookie,
    set_builder_session_cookie,
)

CREDENTIAL = "synthetic_0123456789abcdefghijklmnopqrstuvwxyzABCDEF"


def test_set_cookie_attributes() -> None:
    response = Response()
    response.headers["Cache-Control"] = "public"
    set_builder_session_cookie(response, IssuedBuilderSession(CREDENTIAL, "synthetic-record"))

    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    assert list(cookie) == ["__Host-novy_builder_session"]
    morsel = cookie[BUILDER_SESSION_COOKIE]
    assert morsel.value == CREDENTIAL
    assert morsel["path"] == "/"
    assert morsel["domain"] == ""
    assert morsel["secure"] is True
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "lax"
    assert morsel["max-age"] == str(30 * 24 * 60 * 60)
    assert "synthetic-record" not in response.headers["set-cookie"]
    assert "access_token" not in response.headers["set-cookie"]
    assert "refresh_token" not in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


def test_clear_cookie_attributes() -> None:
    response = Response()
    clear_builder_session_cookie(response)

    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    assert list(cookie) == [BUILDER_SESSION_COOKIE]
    morsel = cookie[BUILDER_SESSION_COOKIE]
    assert morsel.value == ""
    assert morsel["path"] == "/"
    assert morsel["domain"] == ""
    assert morsel["secure"] is True
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "lax"
    assert morsel["max-age"] == "0"
    assert morsel["expires"]
    assert response.headers["cache-control"] == "no-store"


def test_cookie_jar_sends_only_over_https_and_to_origin_host() -> None:
    response = Response()
    set_builder_session_cookie(response, IssuedBuilderSession(CREDENTIAL, "synthetic-record"))

    class CookieResponse:
        def info(self) -> Message:
            headers = Message()
            headers.add_header("Set-Cookie", response.headers["set-cookie"])
            return headers

    jar = CookieJar()
    jar.extract_cookies(CookieResponse(), Request("https://example.test/"))  # type: ignore[arg-type]
    https = Request("https://example.test/somewhere")
    http = Request("http://example.test/somewhere")
    other_host = Request("https://other.test/")
    for request in (https, http, other_host):
        jar.add_cookie_header(request)
    assert https.get_header("Cookie") == f"{BUILDER_SESSION_COOKIE}={CREDENTIAL}"
    assert http.get_header("Cookie") is None
    assert other_host.get_header("Cookie") is None


def test_cookie_module_does_not_register_a_public_endpoint() -> None:
    app = FastAPI()
    original_routes = list(app.routes)
    assert not hasattr(builder_session_cookie, "router")
    assert app.routes == original_routes
