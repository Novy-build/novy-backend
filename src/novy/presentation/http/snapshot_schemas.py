"""Snapshot HTTP schemas (DTOs)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    projectId: str
    environment: str
    schemaVersion: int
    sourceRevision: int
    rendererVersion: str
    capabilityVersion: str
    createdAt: str
    createdBy: str


class ActivateSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshotId: str


class HostedStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectId: str
    environment: str
    activeSnapshotId: str | None = None
    hostedUrl: str | None = None
    status: str = "inactive"
