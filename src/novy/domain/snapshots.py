"""Immutable prototype snapshot domain models.

A snapshot captures a validated project document, data schemas, access
policies, renderer/capability version, and source revision. Once created,
a snapshot's configuration never changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from novy.domain.projects import _valid_datetime, _valid_text


class SnapshotError(Exception):
    """Raised when a snapshot operation fails."""

    def __init__(self, message: str = "Snapshot operation failed") -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PrototypeSnapshot:
    """An immutable validated prototype snapshot."""

    id: str
    project_id: str
    environment: str
    schema_version: int
    source_revision: int
    renderer_version: str
    capability_version: str
    project_document: dict[str, Any]
    data_schemas: list[dict[str, Any]]
    policies: list[dict[str, Any]]
    created_at: datetime
    created_by: str

    def __post_init__(self) -> None:
        if not _valid_text(self.id):
            raise ValueError("Invalid snapshot id")
        if not _valid_text(self.project_id):
            raise ValueError("Invalid project id")
        if self.environment not in ("preview", "live"):
            raise ValueError("Invalid environment")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ValueError("Invalid schema version")
        if not isinstance(self.source_revision, int) or isinstance(self.source_revision, bool):
            raise ValueError("Invalid source revision")
        if not _valid_text(self.renderer_version):
            raise ValueError("Invalid renderer version")
        if not _valid_text(self.capability_version):
            raise ValueError("Invalid capability version")
        if not isinstance(self.project_document, dict):
            raise ValueError("Invalid project document")
        if not isinstance(self.data_schemas, list):
            raise ValueError("Invalid data schemas")
        if not isinstance(self.policies, list):
            raise ValueError("Invalid policies")
        if not _valid_datetime(self.created_at):
            raise ValueError("Invalid created_at")
        if not _valid_text(self.created_by):
            raise ValueError("Invalid created_by")
