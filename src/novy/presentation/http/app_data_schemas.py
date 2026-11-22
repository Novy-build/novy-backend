"""Transport shapes for project-scoped application data."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class DataFieldResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    key: str
    label: str
    type: str
    required: bool = False


class RecordIdentifierResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    type: str = "string"
    generation: str = "backend"


class ProjectDatabaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    projectId: str
    name: str
    createdAt: str
    updatedAt: str


class DataCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    projectId: str
    databaseId: str
    name: str
    fields: list[DataFieldResponse]
    identifier: RecordIdentifierResponse | None = None
    createdAt: str
    updatedAt: str


class DataRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    projectId: str
    collectionId: str
    values: dict[str, Any]
    createdAt: str
    updatedAt: str


class AppDataSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    databases: list[ProjectDatabaseResponse]
    collections: list[DataCollectionResponse]
    recordsByCollection: dict[str, list[DataRecordResponse]]


class CreateDatabaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class UpdateDatabaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class CreateCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    databaseId: str | None = None
    fields: list[DataFieldResponse] = []
    identifier: RecordIdentifierResponse | None = None


class UpdateCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    fields: list[DataFieldResponse] = []
    identifier: RecordIdentifierResponse | None = None


class CreateRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


class UpdateRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]
