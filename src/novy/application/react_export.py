"""Use case for generating a React source archive from a validated manifest."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, cast

from novy.application.projects import ProjectService
from novy.domain.generation import GeneratedArchive
from novy.domain.manifest_validation import validate_manifest
from novy.domain.projects import ProjectNotFoundError
from novy.infrastructure.rate_limit import RateLimiter

EXPORT_RATE_LIMIT_OPERATION = "exports.react"
DEFAULT_DEADLINE_SECONDS = 25.0


class ExportNotFoundError(Exception):
    """The project does not exist for this builder."""


class ExportUnavailableError(Exception):
    """A required dependency is not configured."""


class ExportRateLimitError(Exception):
    """The builder has exhausted the export rate limit."""


class ExportTimeoutError(Exception):
    """Generation exceeded the deadline."""


class SourceGenerator(Protocol):
    def generate(self, manifest: dict[str, object]) -> GeneratedArchive: ...


class ArchiveWriter(Protocol):
    def write(self, archive: GeneratedArchive) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ReactExportResult:
    """The finalized ZIP response payload."""

    filename: str
    content: bytes


class ReactExportService:
    """Validate, authorize, generate and package a React export."""

    def __init__(
        self,
        *,
        projects: ProjectService | None,
        generator: SourceGenerator,
        archives: ArchiveWriter,
        rate_limiter: RateLimiter,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    ) -> None:
        self._projects = projects
        self._generator = generator
        self._archives = archives
        self._rate_limiter = rate_limiter
        self._deadline_seconds = deadline_seconds

    async def export(self, manifest: dict[str, object], owner_id: str) -> ReactExportResult:
        normalized = validate_manifest(manifest)
        project = cast(dict[str, object], normalized["project"])
        project_id = cast(str, project["id"])

        if self._projects is None:
            raise ExportUnavailableError()
        try:
            await self._projects.get_project(project_id, owner_id)
        except ProjectNotFoundError as exc:
            raise ExportNotFoundError() from exc

        if not self._rate_limiter.check(owner_id, EXPORT_RATE_LIMIT_OPERATION):
            raise ExportRateLimitError()

        def build() -> ReactExportResult:
            generated = self._generator.generate(normalized)
            return ReactExportResult(
                filename=generated.filename, content=self._archives.write(generated)
            )

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(build), timeout=self._deadline_seconds
            )
        except TimeoutError as exc:
            raise ExportTimeoutError() from exc
