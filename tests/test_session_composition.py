import base64
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pymongo.asynchronous.database import AsyncDatabase

from novy.application.builder_sessions import BuilderSessionService
from novy.composition import create_builder_session_service
from novy.infrastructure.settings import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in tuple(os.environ):
        if name.upper().startswith(("NOVY_", "MONGO", "SUPABASE", "NEXT_PUBLIC_SUPABASE_")):
            monkeypatch.delenv(name, raising=False)


def _settings(*, database: bool = True) -> Settings:
    return Settings(
        environment="test",
        mongodb_connection_string=SecretStr("mongodb://synthetic.example.test")
        if database
        else None,
        session_encryption_key=SecretStr(base64.urlsafe_b64encode(b"e" * 32).decode()),
        session_lookup_key=SecretStr(base64.urlsafe_b64encode(b"l" * 32).decode()),
    )


def test_session_factory_requires_explicit_keys() -> None:
    database = cast(AsyncDatabase[dict[str, Any]], MagicMock())
    with pytest.raises(RuntimeError, match="Builder session security is not configured"):
        create_builder_session_service(database, Settings(environment="test"))


def test_lifespan_wires_and_clears_session_service(monkeypatch: pytest.MonkeyPatch) -> None:
    import novy.main as main

    client = MagicMock()
    client.close = AsyncMock()
    monkeypatch.setattr(main, "create_mongo_client", MagicMock(return_value=client))
    app = main.create_app(_settings())
    with TestClient(app):
        assert isinstance(app.state.builder_session_service, BuilderSessionService)
        db = client.__getitem__.return_value
        assert db.__getitem__.call_count == 4
        db.__getitem__.assert_any_call("builder_sessions")
        db.__getitem__.assert_any_call("builder_profiles")
    assert app.state.builder_session_service is None
    client.close.assert_awaited_once()


def test_no_keys_or_database_means_no_session_service(monkeypatch: pytest.MonkeyPatch) -> None:
    import novy.main as main

    client = MagicMock()
    client.close = AsyncMock()
    monkeypatch.setattr(main, "create_mongo_client", MagicMock(return_value=client))
    without_keys = main.create_app(
        Settings(environment="test", mongodb_connection_string=SecretStr("mongodb://example.test"))
    )
    without_database = main.create_app(_settings(database=False))
    with TestClient(without_keys):
        assert without_keys.state.builder_session_service is None
    with TestClient(without_database):
        assert without_database.state.builder_session_service is None
