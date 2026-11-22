"""Unit tests for project use cases (ProjectService)."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from novy.application.projects import (
    CreateProjectInput,
    ProjectService,
)
from novy.domain.projects import (
    Project,
    ProjectDataError,
    ProjectDraft,
    ProjectNotFoundError,
    ProjectRevision,
)

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


def _project(**overrides: object) -> Project:
    defaults: dict[str, object] = {
        "id": "synthetic-project",
        "owner_id": "synthetic-owner",
        "name": "Synthetic Project",
        "created_at": NOW,
        "updated_at": NOW,
        "draft": ProjectDraft(content={"screens": []}),
        "schema_version": 14,
        "revision": ProjectRevision(value=0),
        "lifecycle_state": "active",
        "project_type": "app",
    }
    defaults.update(overrides)
    return Project(**defaults)  # type: ignore[arg-type]


def _repo() -> MagicMock:
    fake = MagicMock()
    fake.create = AsyncMock()
    fake.find_by_id_and_owner = AsyncMock()
    fake.find_by_owner = AsyncMock()
    fake.save_with_revision = AsyncMock()
    fake.rename = AsyncMock()
    fake.delete = AsyncMock()
    fake.exists = AsyncMock()
    return fake


def test_create_project() -> None:
    repo = _repo()
    project = _project()
    repo.create.return_value = project
    service = ProjectService(repo)
    result = asyncio.run(
        service.create_project(
            CreateProjectInput(
                owner_id="synthetic-owner",
                name="Synthetic Project",
                draft=ProjectDraft(content={"screens": []}),
            )
        )
    )
    assert result.name == "Synthetic Project"
    assert result.id == "synthetic-project"


def test_create_project_rejects_empty_name() -> None:
    repo = _repo()
    service = ProjectService(repo)
    with pytest.raises(ProjectDataError):
        asyncio.run(
            service.create_project(
                CreateProjectInput(
                    owner_id="synthetic-owner",
                    name="",
                    draft=ProjectDraft(content={"screens": []}),
                )
            )
        )


def test_get_project_not_found() -> None:
    repo = _repo()
    repo.find_by_id_and_owner.return_value = None
    service = ProjectService(repo)
    with pytest.raises(ProjectNotFoundError):
        asyncio.run(service.get_project("missing", "synthetic-owner"))


def test_list_projects() -> None:
    repo = _repo()
    repo.find_by_owner.return_value = [_project()]
    service = ProjectService(repo)
    result = asyncio.run(service.list_projects("synthetic-owner"))
    assert len(result) == 1


def test_save_document() -> None:
    repo = _repo()
    project = _project(revision=ProjectRevision(value=1))
    repo.save_with_revision.return_value = project
    service = ProjectService(repo)
    result = asyncio.run(
        service.save_document(
            "synthetic-project",
            "synthetic-owner",
            0,
            ProjectDraft(content={"screens": []}),
        )
    )
    assert result.revision == 1


def test_rename_project() -> None:
    repo = _repo()
    project = _project(name="Renamed")
    repo.rename.return_value = project
    service = ProjectService(repo)
    result = asyncio.run(service.rename_project("synthetic-project", "synthetic-owner", "Renamed"))
    assert result.name == "Renamed"


def test_rename_project_rejects_empty() -> None:
    repo = _repo()
    service = ProjectService(repo)
    with pytest.raises(ProjectDataError):
        asyncio.run(service.rename_project("synthetic-project", "synthetic-owner", ""))


def test_delete_project() -> None:
    repo = _repo()
    service = ProjectService(repo)
    asyncio.run(service.delete_project("synthetic-project", "synthetic-owner"))
    repo.delete.assert_awaited_once()


def test_duplicate_project() -> None:
    repo = _repo()
    source = _project()
    repo.find_by_id_and_owner.return_value = source
    new_project = _project(id="copy-project", name="Synthetic Project (copy)")
    repo.create.return_value = new_project
    service = ProjectService(repo)
    result = asyncio.run(service.duplicate_project("synthetic-project", "synthetic-owner"))
    assert result.id == "copy-project"


def test_duplicate_project_not_found() -> None:
    repo = _repo()
    repo.find_by_id_and_owner.return_value = None
    service = ProjectService(repo)
    with pytest.raises(ProjectNotFoundError):
        asyncio.run(service.duplicate_project("missing", "synthetic-owner"))
