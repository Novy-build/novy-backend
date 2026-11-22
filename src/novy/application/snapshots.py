"""Prototype snapshot persistence port and use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from novy.domain.snapshots import PrototypeSnapshot, SnapshotError


class SnapshotStoreError(Exception):
    """Raised when snapshot storage is unavailable."""

    def __init__(self) -> None:
        super().__init__("Snapshot storage is unavailable")


class SnapshotRepository(Protocol):
    """Port for snapshot persistence."""

    async def create(self, snapshot: PrototypeSnapshot) -> PrototypeSnapshot: ...

    async def find_by_id(self, snapshot_id: str, project_id: str) -> PrototypeSnapshot | None: ...

    async def find_active(self, project_id: str, environment: str) -> PrototypeSnapshot | None: ...

    async def set_active(self, project_id: str, environment: str, snapshot_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class CreateSnapshotInput:
    project_id: str
    environment: str
    schema_version: int
    source_revision: int
    renderer_version: str
    capability_version: str
    project_document: dict[str, Any]
    data_schemas: list[dict[str, Any]]
    policies: list[dict[str, Any]]
    created_by: str


class SnapshotService:
    """Use cases for snapshot creation and activation."""

    def __init__(self, repository: SnapshotRepository) -> None:
        self._repository = repository

    async def create_snapshot(self, input: CreateSnapshotInput) -> PrototypeSnapshot:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        snapshot = PrototypeSnapshot(
            id="",  # Will be assigned by repository
            project_id=input.project_id,
            environment=input.environment,
            schema_version=input.schema_version,
            source_revision=input.source_revision,
            renderer_version=input.renderer_version,
            capability_version=input.capability_version,
            project_document=input.project_document,
            data_schemas=input.data_schemas,
            policies=input.policies,
            created_at=now,
            created_by=input.created_by,
        )
        return await self._repository.create(snapshot)

    async def get_active_snapshot(
        self, project_id: str, environment: str
    ) -> PrototypeSnapshot | None:
        return await self._repository.find_active(project_id, environment)

    async def activate_snapshot(self, project_id: str, environment: str, snapshot_id: str) -> None:
        snapshot = await self._repository.find_by_id(snapshot_id, project_id)
        if snapshot is None:
            raise SnapshotError("Snapshot not found")
        if snapshot.environment != environment:
            raise SnapshotError("Snapshot environment mismatch")
        await self._repository.set_active(project_id, environment, snapshot_id)
