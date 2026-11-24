"""HTTP contract tests for the React export route."""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novy.application.projects import ProjectService
from novy.application.react_export import (
    EXPORT_RATE_LIMIT_OPERATION,
    ReactExportService,
)
from novy.domain.generation import GeneratedArchive
from novy.infrastructure.rate_limit import RateLimiter
from novy.infrastructure.react_source.generator import ReactSourceGenerator
from novy.infrastructure.react_source.zip_archive import ZipArchiveWriter
from novy.infrastructure.settings import Settings
from novy.main import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "react_export" / "manifest_v2.json"
CREDENTIAL = "a" * 43
ORIGIN = "http://localhost:5173"


def load_manifest() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakeProjects:
    async def get_project(self, project_id: str, owner_id: str) -> object:
        return object()


class FakeGenerator:
    def __init__(self, *, delay: float = 0.0, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail

    def generate(self, manifest: dict[str, object]) -> GeneratedArchive:
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("generation exploded")
        return ReactSourceGenerator().generate(manifest)


def make_service(
    *, capacity: int = 100, generator: FakeGenerator | None = None, deadline: float = 25.0
) -> ReactExportService:
    limiter = RateLimiter()
    limiter.configure(EXPORT_RATE_LIMIT_OPERATION, capacity, 0.0)
    return ReactExportService(
        projects=_projects(),
        generator=generator or FakeGenerator(),
        archives=ZipArchiveWriter(),
        rate_limiter=limiter,
        deadline_seconds=deadline,
    )


def _projects() -> ProjectService:
    from typing import cast

    return cast(ProjectService, FakeProjects())


async def _allow_guards(request: Any) -> None:
    return None


@contextmanager
def make_client(
    monkeypatch: pytest.MonkeyPatch,
    service: ReactExportService | None = None,
    *,
    patch_guards: bool = True,
    patch_credential: bool = True,
) -> Generator[TestClient, None, None]:
    import novy.presentation.http.exports as exports_routes

    if patch_guards:
        monkeypatch.setattr(exports_routes, "require_unsafe_request_guards", _allow_guards)
    if patch_credential:
        monkeypatch.setattr(exports_routes, "get_session_credential", lambda request: CREDENTIAL)
    app = create_app(Settings(environment="test"))
    with TestClient(app) as client:
        app.state.react_export_service = service or make_service()
        yield client


def test_route_is_wired() -> None:
    app = create_app(Settings(environment="test"))
    assert "/api/v1/exports/react" in app.openapi()["paths"]


def test_success_returns_deterministic_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    with make_client(monkeypatch) as client:
        response = client.post("/api/v1/exports/react", json=load_manifest())
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="notes-react-app.zip"'
    assert response.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert "src/generated/App.tsx" in archive.namelist()


def test_missing_origin_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with make_client(monkeypatch, patch_guards=False) as client:
        response = client.post("/api/v1/exports/react", json=load_manifest())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"


def test_bad_csrf_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with make_client(monkeypatch, patch_guards=False) as client:
        response = client.post(
            "/api/v1/exports/react", json=load_manifest(), headers={"Origin": ORIGIN}
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_missing_session_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with make_client(monkeypatch, patch_credential=False) as client:
        response = client.post("/api/v1/exports/react", json=load_manifest())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_required"


def test_invalid_manifest_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = load_manifest()
    manifest["manifestVersion"] = 1
    with make_client(monkeypatch) as client:
        response = client.post("/api/v1/exports/react", json=manifest)
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"validation_error", "invalid_manifest"}


def test_deferred_capability_returns_validation_message(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = load_manifest()
    manifest["runtime"]["requirements"].append(
        {
            "capability": "rag",
            "configurationOwner": "backend",
            "requiredBinding": "rag.knowledge-index",
            "requiredBy": ["node_list"],
        }
    )
    with make_client(monkeypatch) as client:
        response = client.post("/api/v1/exports/react", json=manifest)
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert "rag" in body["message"]


def test_rate_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    with make_client(monkeypatch, make_service(capacity=1)) as client:
        assert client.post("/api/v1/exports/react", json=load_manifest()).status_code == 200
        limited = client.post("/api/v1/exports/react", json=load_manifest())
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"


def test_oversized_body_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = json.dumps(load_manifest()) + " " + "x" * (1024 * 1024 + 1)
    with make_client(monkeypatch) as client:
        response = client.post(
            "/api/v1/exports/react",
            content=oversized.encode(),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_generator_failure_maps_to_generation_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service(generator=FakeGenerator(fail=True))
    with make_client(monkeypatch, service) as client:
        response = client.post("/api/v1/exports/react", json=load_manifest())
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "generation_failed"


def test_timeout_maps_to_generation_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service(generator=FakeGenerator(delay=0.05), deadline=0.01)
    with make_client(monkeypatch, service) as client:
        response = client.post("/api/v1/exports/react", json=load_manifest())
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "generation_timeout"
