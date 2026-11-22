"""Project import: two-pass typed remapping with idempotency.

Accepts the frontend's ``{project, appData}`` payload, allocates fresh
ObjectIds, remaps typed references, and persists atomically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class ImportStoreError(Exception):
    """Raised when import storage is unavailable."""

    def __init__(self) -> None:
        super().__init__("Import storage is unavailable")


class ImportConflictError(Exception):
    """Raised when an idempotency key is reused with a different payload."""

    def __init__(self) -> None:
        super().__init__("Import idempotency conflict")


@dataclass(frozen=True, slots=True)
class ImportPayload:
    """Validated import payload from the frontend."""

    project: dict[str, Any]
    app_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Result of a successful import."""

    project_id: str
    name: str
    idempotency_key: str


class ImportRepository(Protocol):
    """Port for transactional import."""

    async def import_project(
        self,
        owner_id: str,
        payload: ImportPayload,
        idempotency_key: str,
    ) -> str: ...


class ImportService:
    """Orchestrates validation, remapping, and atomic import."""

    def __init__(self, store: ImportRepository) -> None:
        self._store = store

    def validate_payload(self, payload: ImportPayload) -> None:
        if not isinstance(payload.project, dict):
            raise ValueError("Project payload must be a dict")
        if "name" not in payload.project:
            raise ValueError("Project name is required")
        name = payload.project.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Project name must be a non-empty string")
        schema_version = payload.project.get("schemaVersion", 14)
        if schema_version not in (13, 14):
            raise ValueError(f"Unsupported schema version: {schema_version}")

    async def import_project(
        self,
        owner_id: str,
        payload: ImportPayload,
        idempotency_key: str,
    ) -> ImportResult:
        self.validate_payload(payload)
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("Idempotency key is required")
        project_id = await self._store.import_project(owner_id, payload, idempotency_key.strip())
        name = payload.project.get("name", "")
        return ImportResult(
            project_id=project_id,
            name=name if isinstance(name, str) else "",
            idempotency_key=idempotency_key.strip(),
        )
