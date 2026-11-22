"""Project HTTP schemas (DTOs)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    projectType: str = "app"
    createdAt: str
    updatedAt: str
    schemaVersion: int
    revision: int
    lifecycleState: str = "active"
    draft: dict[str, object] = {}


class ProjectSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    projectType: str = "app"
    createdAt: str
    updatedAt: str
    screenCount: int = 0


class ProjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projects: list[ProjectSummaryResponse]


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    template: dict[str, object] | None = None


class RenameProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class SaveDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: int
    screens: list[object] = []
