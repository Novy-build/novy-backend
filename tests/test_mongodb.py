import logging
import os
import socket
import traceback
from pathlib import Path
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from pymongo.errors import PyMongoError

from novy.infrastructure import mongodb
from novy.infrastructure.settings import Settings

URI = "mongodb://synthetic-user:synthetic-password@localhost:27017"
SRV_URI = "mongodb+srv://synthetic-user:synthetic-password@cluster.example.test"


def reject_network(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Unexpected network access")


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in tuple(os.environ):
        if name.upper().startswith(("NOVY_", "MONGO", "SUPABASE", "NEXT_PUBLIC_SUPABASE_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)


def test_settings_missing_and_source_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert Settings().mongodb_connection_string is None
    (tmp_path / ".env").write_text(
        f"MONGODB_CONNECTION_STRING={URI}\nNOVY_ENVIRONMENT=development\n", encoding="utf-8"
    )
    settings = Settings()
    assert settings.mongodb_connection_string == SecretStr(URI)
    assert settings.environment == "development"

    monkeypatch.setenv("MONGODB_CONNECTION_STRING", SRV_URI)
    monkeypatch.setenv("NOVY_ENVIRONMENT", "test")
    settings = Settings()
    assert settings.mongodb_connection_string == SecretStr(SRV_URI)
    assert settings.environment == "test"
    settings = Settings(mongodb_connection_string=SecretStr(URI), environment="production")
    assert settings.mongodb_connection_string == SecretStr(URI)
    assert settings.environment == "production"


@pytest.mark.parametrize(
    "uri",
    [
        URI,
        SRV_URI,
        URI + "?tls=true&ssl=true",
        URI + "?tlsInsecure=false&tlsAllowInvalidCertificates=false&tlsAllowInvalidHostnames=false",
    ],
)
def test_settings_accept_secure_uris(uri: str) -> None:
    settings = Settings(mongodb_connection_string=SecretStr(uri))
    assert settings.mongodb_connection_string == SecretStr(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "   ",
        "https://localhost",
        "mongodb://",
        "mongodb+srv://",
        "mongodb://[invalid",
        URI + "?tls=false",
        URI + "?SSL=FALSE",
        URI + "?tlsInsecure=true",
        URI + "?tlsAllowInvalidCertificates=true",
        URI + "?tlsAllowInvalidHostnames=true",
        URI + "?appName=example;tlsInsecure=true",
        URI + "?appName=example;tlsAllowInvalidCertificates=true",
        URI + "?appName=example;tlsAllowInvalidHostnames=true",
        URI + "?appName=example#tag&tlsInsecure=true",
        URI + "#tag?tlsAllowInvalidCertificates=true",
        URI + "?%74lsInsecure=true",
        SRV_URI + "?srvAllowedHostsSuffix=example.test",
    ],
)
def test_settings_reject_invalid_or_insecure_uris(uri: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(mongodb_connection_string=SecretStr(uri))
    assert "synthetic-password" not in str(exc_info.value)
    assert "synthetic-password" not in repr(exc_info.value.errors())
    assert "synthetic-password" not in exc_info.value.json()
    if uri.strip():
        assert uri not in str(exc_info.value)


def test_invalid_dotenv_uri_masks_structured_error(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        f"MONGODB_CONNECTION_STRING={URI}?tlsInsecure=true\n", encoding="utf-8"
    )
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "synthetic-password" not in repr(exc_info.value.errors())
    assert "synthetic-password" not in exc_info.value.json()


def test_invalid_bytes_uri_masks_structured_error() -> None:
    uri = (URI + "?tlsInsecure=true").encode()
    with pytest.raises(ValidationError) as exc_info:
        Settings.model_validate({"mongodb_connection_string": uri})
    assert "synthetic-password" not in repr(exc_info.value.errors())
    assert "synthetic-password" not in exc_info.value.json()


@pytest.mark.parametrize("value", [[URI], {"uri": URI}])
def test_invalid_container_uri_masks_structured_error(value: object) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings.model_validate({"mongodb_connection_string": value})
    assert "synthetic-password" not in repr(exc_info.value.errors())
    assert "synthetic-password" not in exc_info.value.json()


def test_supabase_dotenv_is_accepted_and_excluded(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "NEXT_PUBLIC_SUPABASE_URL=https://synthetic.example.test\n"
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=synthetic-publishable-key\n",
        encoding="utf-8",
    )
    settings = Settings()
    assert settings.supabase_url == SecretStr("https://synthetic.example.test")
    assert settings.supabase_publishable_key == SecretStr("synthetic-publishable-key")
    assert "supabase_url" not in settings.model_dump()
    assert "supabase_publishable_key" not in settings.model_dump()
    assert "synthetic.example.test" not in repr(settings)
    assert "synthetic-publishable-key" not in repr(settings)
    assert "synthetic-publishable-key" not in settings.model_dump_json()


def test_unknown_dotenv_key_is_forbidden_without_value(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("UNRELATED_KEY=synthetic-sensitive-value\n", encoding="utf-8")
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert any(error["type"] == "extra_forbidden" for error in exc_info.value.errors())
    assert "synthetic-sensitive-value" not in str(exc_info.value)


def test_client_constructor_options(monkeypatch: pytest.MonkeyPatch) -> None:
    constructor = MagicMock()
    monkeypatch.setattr(mongodb, "AsyncMongoClient", constructor)
    client = mongodb.create_mongo_client(SecretStr(URI))
    assert client is constructor.return_value
    constructor.assert_called_once_with(
        URI,
        tls=True,
        maxPoolSize=10,
        connectTimeoutMS=2000,
        serverSelectionTimeoutMS=3000,
        waitQueueTimeoutMS=3000,
        timeoutMS=5000,
        appname="novy-backend",
    )
    assert mongodb.PLATFORM_DATABASE_NAME == "platform_db"


@pytest.mark.parametrize("error_type", [PyMongoError, ValueError, TypeError])
def test_constructor_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    constructor = MagicMock(side_effect=error_type(URI))
    monkeypatch.setattr(mongodb, "AsyncMongoClient", constructor)
    with pytest.raises(RuntimeError) as exc_info:
        mongodb.create_mongo_client(SecretStr(URI))
    error = exc_info.value
    assert error.__cause__ is None
    assert error.__suppress_context__
    assert URI not in str(error)
    assert "synthetic-password" not in "".join(traceback.format_exception(error))


def mongo_mock() -> MagicMock:
    client = MagicMock()
    client.close = AsyncMock()
    return client


def test_lifespan_liveness_and_repeated_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import novy.main as main

    first, second = mongo_mock(), mongo_mock()
    factory = MagicMock(side_effect=[first, second])
    monkeypatch.setattr(main, "create_mongo_client", factory)
    app = main.create_app(Settings(mongodb_connection_string=SecretStr(URI)))
    for expected in (first, second):
        with TestClient(app) as client:
            assert app.state.mongo_client is expected
            assert app.state.platform_database is expected.__getitem__.return_value
            expected.__getitem__.assert_called_once_with("platform_db")
            with monkeypatch.context() as network:
                network.setattr(socket.socket, "connect", reject_network)
                network.setattr(socket.socket, "connect_ex", reject_network)
                response = client.get("/api/v1/health/live")
            assert response.status_code == 200
            assert response.json() == {"status": "alive"}
            assert client.get("/api/v1/health/ready").status_code == 404
            assert len(expected.mock_calls) == 18
        expected.close.assert_awaited_once_with()
        assert app.state.mongo_client is None
        assert app.state.platform_database is None
    assert factory.call_count == 2
    assert factory.call_args_list[0].args == (SecretStr(URI),)


def test_missing_configuration_and_independent_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    import novy.main as main

    configured_client = mongo_mock()
    factory = MagicMock(return_value=configured_client)
    monkeypatch.setattr(main, "create_mongo_client", factory)
    empty = main.create_app(Settings())
    configured = main.create_app(Settings(mongodb_connection_string=SecretStr(URI)))
    with TestClient(empty) as empty_client, TestClient(configured) as configured_test_client:
        assert empty.state.mongo_client is None
        assert empty.state.platform_database is None
        assert configured.state.mongo_client is configured_client
        assert empty_client.get("/api/v1/health/live").status_code == 200
        assert configured_test_client.get("/api/v1/health/live").status_code == 200
        factory.assert_called_once()
    configured_client.close.assert_awaited_once_with()
    assert configured.state.mongo_client is None
    assert configured.state.platform_database is None


@pytest.mark.parametrize("uri", [URI, SRV_URI])
def test_real_client_lifecycle_does_not_connect_on_liveness(uri: str) -> None:
    from novy.main import create_app

    app = create_app(Settings(mongodb_connection_string=SecretStr(uri)))
    with TestClient(app) as client:
        assert app.state.platform_database.name == "platform_db"
        assert client.get("/api/v1/health/live").status_code == 200
    assert app.state.mongo_client is None
    assert app.state.platform_database is None


def test_database_selection_failure_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import novy.main as main

    fake = mongo_mock()
    fake.__getitem__.side_effect = RuntimeError("selection failed")
    monkeypatch.setattr(main, "create_mongo_client", MagicMock(return_value=fake))
    app = main.create_app(Settings(mongodb_connection_string=SecretStr(URI)))
    with pytest.raises(RuntimeError, match="selection failed"), TestClient(app):
        pass
    fake.close.assert_awaited_once_with()
    assert app.state.mongo_client is None
    assert app.state.platform_database is None


def test_cleanup_failure_logs_only_safe_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import novy.main as main

    fake = mongo_mock()
    fake.close.side_effect = RuntimeError(URI)
    monkeypatch.setattr(main, "create_mongo_client", MagicMock(return_value=fake))
    app = main.create_app(Settings(mongodb_connection_string=SecretStr(URI)))
    with caplog.at_level(logging.WARNING), TestClient(app):
        pass
    fake.close.assert_awaited_once_with()
    assert app.state.mongo_client is None
    assert app.state.platform_database is None
    assert [record.getMessage() for record in caplog.records] == ["MongoDB client cleanup failed"]
    assert all(record.exc_info is None for record in caplog.records)
    assert "synthetic-password" not in caplog.text
