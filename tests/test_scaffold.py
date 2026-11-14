import os
import socket
from importlib import import_module
from pathlib import Path
from typing import Literal, NoReturn

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from novy.infrastructure.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(PROJECT_ROOT))
    for name in tuple(os.environ):
        if name.upper().startswith(("NOVY_", "MONGO", "SUPABASE", "NEXT_PUBLIC_SUPABASE_")):
            monkeypatch.delenv(name, raising=False)


def reject_network(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Liveness must not make network connections")


@pytest.mark.parametrize("environment", ["development", "test", "production"])
def test_liveness_without_provider_credentials(
    environment: Literal["development", "test", "production"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)

    from novy.main import create_app

    app = create_app(Settings(environment=environment))
    assert app.debug is False
    with TestClient(app) as client:
        with monkeypatch.context() as network:
            network.setattr(socket.socket, "connect", reject_network)
            network.setattr(socket.socket, "connect_ex", reject_network)
            response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("environment", ["development", "test"])
def test_nonproduction_docs_only_document_liveness(
    environment: Literal["development", "test"],
) -> None:
    from novy.main import create_app

    app = create_app(Settings(environment=environment))
    with TestClient(app) as client:
        docs = client.get("/docs")
        schema = client.get("/openapi.json")
        redoc = client.get("/redoc")

    assert docs.status_code == 200
    assert "/openapi.json" in docs.text
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert set(paths) == {"/api/v1/health/live"}
    assert set(paths["/api/v1/health/live"]) == {"get"}
    assert redoc.status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"])
def test_production_docs_are_disabled(path: str) -> None:
    from novy.main import create_app

    app = create_app(Settings(environment="production"))
    with TestClient(app) as client:
        assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects",
        "/api/v1/auth/session",
        "/api/v1/health/ready",
        "/api/v1/health/readiness",
    ],
)
def test_unimplemented_routes_are_absent(path: str) -> None:
    from novy.main import create_app

    app = create_app(Settings(environment="test"))
    with TestClient(app) as client:
        assert client.get(path).status_code == 404


def test_factory_uses_environment_when_settings_are_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novy.main import create_app

    monkeypatch.setenv("NOVY_ENVIRONMENT", "production")
    app = create_app()
    assert app.debug is False
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404


def test_factories_do_not_share_mutable_app_state() -> None:
    from novy.main import create_app

    settings = Settings(environment="test")
    first = create_app(settings)
    second = create_app(settings)

    assert first is not second
    assert first.state is not second.state
    first.state.test_marker = ["first"]
    assert not hasattr(second.state, "test_marker")

    first_schema = first.openapi()
    second_schema = second.openapi()
    assert first_schema is not second_schema
    first_schema["paths"].clear()
    assert "/api/v1/health/live" in second_schema["paths"]


def test_vercel_entrypoint_reexports_module_app() -> None:
    from novy.main import app

    assert import_module("src.main").app is app
    assert app.debug is False


def test_settings_default_to_production() -> None:
    from novy.main import create_app

    assert Settings().environment == "production"
    with TestClient(create_app()) as client:
        assert client.get("/docs").status_code == 404


def test_settings_dotenv_and_environment_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("NOVY_ENVIRONMENT=production\n", encoding="utf-8")
    assert Settings().environment == "production"

    monkeypatch.setenv("NOVY_ENVIRONMENT", "test")
    assert Settings().environment == "test"
    assert Settings(environment="development").environment == "development"

    monkeypatch.delenv("NOVY_ENVIRONMENT")
    assert Settings().environment == "production"


@pytest.mark.parametrize("value", ["staging", "", "PRODUCTION"])
def test_settings_reject_invalid_environment(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("NOVY_ENVIRONMENT", value)
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert any(error["loc"] == ("environment",) for error in exc_info.value.errors())
    if value:
        assert value not in str(exc_info.value)


def test_settings_forbid_unknown_dotenv_entries_and_decode_utf8(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("NOVY_UNEXPECTED=\u2603\n", encoding="utf-8")
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["type"] == "extra_forbidden"
    assert "\u2603" not in str(exc_info.value)
