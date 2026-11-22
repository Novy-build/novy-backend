"""Project duplication: schema-only and explicit copy-records.

Handles copying a project with fresh persisted ObjectIds, remapping typed
references, and optionally copying records. All operations are owner-scoped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DuplicateStoreError(Exception):
    """Raised when duplication storage is unavailable."""

    def __init__(self) -> None:
        super().__init__("Duplication storage is unavailable")


class DuplicateRepository(Protocol):
    """Port for transactional duplication."""

    async def duplicate_schema(
        self,
        source_project_id: str,
        owner_id: str,
        new_name: str,
        *,
        copy_records: bool = False,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class DuplicateResult:
    project_id: str
    name: str
    databases_copied: int
    collections_copied: int
    records_copied: int


class DuplicateService:
    """Orchestrates schema-only and copy-records duplication."""

    def __init__(self, store: DuplicateRepository) -> None:
        self._store = store

    async def duplicate(
        self,
        source_project_id: str,
        owner_id: str,
        new_name: str,
        *,
        copy_records: bool = False,
    ) -> DuplicateResult:
        if not new_name or not new_name.strip():
            raise ValueError("Project name is required")
        if not owner_id or not owner_id.strip():
            raise ValueError("Owner ID is required")
        project_id = await self._store.duplicate_schema(
            source_project_id,
            owner_id,
            new_name.strip(),
            copy_records=copy_records,
        )
        return DuplicateResult(
            project_id=project_id,
            name=new_name.strip(),
            databases_copied=0,
            collections_copied=0,
            records_copied=0,
        )
