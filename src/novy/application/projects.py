"""Project persistence port and use-case contracts.

Defines the ``ProjectRepository`` protocol, application-level exceptions,
and project use cases. Infrastructure adapters implement the protocol;
use cases depend on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from novy.domain.projects import (
    Project,
    ProjectDataError,
    ProjectDraft,
    ProjectNotFoundError,
)


class ProjectStoreError(Exception):
    """Raised when project storage is unavailable."""

    def __init__(self) -> None:
        super().__init__("Project storage is unavailable")


class ProjectRepository(Protocol):
    """Owner-scoped project persistence port.

    All operations are scoped to the server-derived owner. Never trust
    client-supplied owner, role, or tenant fields.
    """

    async def create(
        self, owner_id: str, name: str, draft: ProjectDraft, *, project_type: str = "app"
    ) -> Project: ...

    async def find_by_id_and_owner(self, project_id: str, owner_id: str) -> Project | None: ...

    async def find_by_owner(
        self, owner_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> list[Project]: ...

    async def save_with_revision(
        self,
        project_id: str,
        owner_id: str,
        expected_revision: int,
        new_draft: ProjectDraft,
    ) -> Project: ...

    async def rename(self, project_id: str, owner_id: str, new_name: str) -> Project: ...

    async def delete(self, project_id: str, owner_id: str) -> None: ...

    async def exists(self, project_id: str, owner_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateProjectInput:
    owner_id: str
    name: str
    draft: ProjectDraft
    project_type: str = "app"


@dataclass(frozen=True, slots=True)
class ProjectView:
    """Read-only view of a project for the presentation layer."""

    id: str
    owner_id: str
    name: str
    created_at: str
    updated_at: str
    schema_version: int
    revision: int
    lifecycle_state: str
    project_type: str
    draft: dict[str, object]


def _to_view(project: Project) -> ProjectView:
    return ProjectView(
        id=project.id,
        owner_id=project.owner_id,
        name=project.name,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
        schema_version=project.schema_version,
        revision=project.revision.value,
        lifecycle_state=project.lifecycle_state,
        project_type=project.project_type,
        draft=project.draft.content,
    )


class ProjectService:
    """Use cases for owner-scoped project management."""

    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    async def create_project(self, input: CreateProjectInput) -> ProjectView:
        if not input.owner_id or not input.owner_id.strip():
            raise ProjectDataError()
        if not input.name or not input.name.strip():
            raise ProjectDataError()
        project = await self._repository.create(
            input.owner_id,
            input.name.strip(),
            input.draft,
            project_type=input.project_type,
        )
        return _to_view(project)

    async def get_project(self, project_id: str, owner_id: str) -> ProjectView:
        project = await self._repository.find_by_id_and_owner(project_id, owner_id)
        if project is None or project.lifecycle_state == "deleted":
            raise ProjectNotFoundError()
        return _to_view(project)

    async def list_projects(
        self, owner_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> list[ProjectView]:
        projects = await self._repository.find_by_owner(owner_id, cursor=cursor, limit=limit)
        return [_to_view(p) for p in projects if p.lifecycle_state != "deleted"]

    async def save_document(
        self,
        project_id: str,
        owner_id: str,
        expected_revision: int,
        new_draft: ProjectDraft,
    ) -> ProjectView:
        project = await self._repository.save_with_revision(
            project_id, owner_id, expected_revision, new_draft
        )
        return _to_view(project)

    async def rename_project(self, project_id: str, owner_id: str, new_name: str) -> ProjectView:
        if not new_name or not new_name.strip():
            raise ProjectDataError()
        project = await self._repository.rename(project_id, owner_id, new_name.strip())
        return _to_view(project)

    async def delete_project(self, project_id: str, owner_id: str) -> None:
        await self._repository.delete(project_id, owner_id)

    async def duplicate_project(
        self, project_id: str, owner_id: str, *, copy_records: bool = False
    ) -> ProjectView:
        # Schema-only duplication: copy project + draft, new IDs, no records.
        # Full record copying is implemented in Phase 4.
        source = await self._repository.find_by_id_and_owner(project_id, owner_id)
        if source is None or source.lifecycle_state == "deleted":
            raise ProjectNotFoundError()
        new_draft = ProjectDraft(content=dict(source.draft.content))
        project = await self._repository.create(
            owner_id,
            f"{source.name} (copy)",
            new_draft,
            project_type=source.project_type,
        )
        return _to_view(project)
