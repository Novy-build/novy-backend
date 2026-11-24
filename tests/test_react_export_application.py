"""Application use-case tests for the React export service."""

from __future__ import annotations

import asyncio
import functools
import json
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast

import pytest

from novy.application.projects import ProjectService
from novy.application.react_export import (
    EXPORT_RATE_LIMIT_OPERATION,
    ExportNotFoundError,
    ExportRateLimitError,
    ExportTimeoutError,
    ExportUnavailableError,
    ReactExportService,
)
from novy.domain.generation import GeneratedArchive, GeneratedFile, UnsupportedCapabilityError
from novy.domain.manifest_validation import InvalidManifestError
from novy.domain.projects import ProjectNotFoundError
from novy.infrastructure.rate_limit import RateLimiter

FIXTURE = Path(__file__).parent / "fixtures" / "react_export" / "manifest_v2.json"
CREDENTIAL = "a" * 43
DEFAULT_DEADLINE = 25.0


def _sync[T](fn: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., T]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def load_manifest() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakeProjects:
    def __init__(self, *, missing: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.missing = missing

    async def get_project(self, project_id: str, owner_id: str) -> object:
        self.calls.append((project_id, owner_id))
        if self.missing:
            raise ProjectNotFoundError()
        return object()


class FakeGenerator:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.calls: list[dict[str, object]] = []
        self.delay = delay

    def generate(self, manifest: dict[str, object]) -> GeneratedArchive:
        self.calls.append(manifest)
        if self.delay:
            time.sleep(self.delay)
        return GeneratedArchive(
            filename="notes-react-app.zip",
            files=(GeneratedFile("src/generated/App.tsx", "export const ok = true;\n"),),
        )


class FakeArchives:
    def __init__(self) -> None:
        self.calls = 0

    def write(self, archive: GeneratedArchive) -> bytes:
        self.calls += 1
        return b"PK\x03\x04fake-archive"


def make_service(
    *,
    projects: FakeProjects | None = None,
    generator: FakeGenerator | None = None,
    archives: FakeArchives | None = None,
    capacity: int = 1,
    deadline: float = DEFAULT_DEADLINE,
) -> ReactExportService:
    limiter = RateLimiter()
    limiter.configure(EXPORT_RATE_LIMIT_OPERATION, capacity, 0.0)
    return ReactExportService(
        projects=cast(ProjectService, projects) if projects is not None else None,
        generator=generator or FakeGenerator(),
        archives=archives or FakeArchives(),
        rate_limiter=limiter,
        deadline_seconds=deadline,
    )


@_sync
async def test_successful_export_validates_authorizes_and_packages() -> None:
    projects, generator, archives = FakeProjects(), FakeGenerator(), FakeArchives()
    result = await make_service(
        projects=projects, generator=generator, archives=archives
    ).export(load_manifest(), CREDENTIAL)

    assert result.filename == "notes-react-app.zip"
    assert result.content.startswith(b"PK")
    assert projects.calls == [("project_1", CREDENTIAL)]
    assert len(generator.calls) == 1
    assert archives.calls == 1


@_sync
async def test_project_not_owned_raises_not_found_without_generating() -> None:
    projects, generator = FakeProjects(missing=True), FakeGenerator()
    with pytest.raises(ExportNotFoundError):
        await make_service(projects=projects, generator=generator).export(
            load_manifest(), CREDENTIAL
        )
    assert generator.calls == []


@_sync
async def test_missing_project_service_is_unavailable() -> None:
    with pytest.raises(ExportUnavailableError):
        await make_service().export(load_manifest(), CREDENTIAL)


@_sync
async def test_rate_limit_blocks_generation() -> None:
    generator = FakeGenerator()
    service = make_service(projects=FakeProjects(), generator=generator)
    await service.export(load_manifest(), CREDENTIAL)
    with pytest.raises(ExportRateLimitError):
        await service.export(load_manifest(), CREDENTIAL)
    assert len(generator.calls) == 1


@_sync
async def test_generation_deadline_is_enforced() -> None:
    service = make_service(
        projects=FakeProjects(), generator=FakeGenerator(delay=0.05), deadline=0.01
    )
    with pytest.raises(ExportTimeoutError):
        await service.export(load_manifest(), CREDENTIAL)


@_sync
async def test_validation_runs_before_authorization() -> None:
    projects, generator = FakeProjects(), FakeGenerator()
    manifest = load_manifest()
    manifest["manifestVersion"] = 1
    with pytest.raises(InvalidManifestError):
        await make_service(projects=projects, generator=generator).export(manifest, CREDENTIAL)
    assert projects.calls == []
    assert generator.calls == []


@_sync
async def test_deferred_capability_propagates_without_authorization() -> None:
    projects, generator = FakeProjects(), FakeGenerator()
    manifest = load_manifest()
    manifest["runtime"]["requirements"].append(
        {
            "capability": "rag",
            "configurationOwner": "backend",
            "requiredBinding": "rag.knowledge-index",
            "requiredBy": ["node_list"],
        }
    )
    with pytest.raises(UnsupportedCapabilityError):
        await make_service(projects=projects, generator=generator).export(manifest, CREDENTIAL)
    assert projects.calls == []
    assert generator.calls == []
