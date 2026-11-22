"""Application data domain models: databases, collections, and records.

Framework-independent entities for project-scoped logical data. Records
store values keyed by field ID, not field label.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from novy.domain.projects import _valid_datetime, _valid_text


class AppDataError(Exception):
    """Raised when application data fails domain validation."""

    def __init__(self) -> None:
        super().__init__("Invalid application data")


@dataclass(frozen=True, slots=True)
class AppDatabase:
    """A logical database within a project."""

    id: str
    project_id: str
    name: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not _valid_text(self.id):
            raise ValueError("Invalid database id")
        if not _valid_text(self.project_id):
            raise ValueError("Invalid project id")
        if not _valid_text(self.name):
            raise ValueError("Invalid database name")
        if not _valid_datetime(self.created_at):
            raise ValueError("Invalid created_at")
        if not _valid_datetime(self.updated_at):
            raise ValueError("Invalid updated_at")


@dataclass(frozen=True, slots=True)
class AppCollection:
    """A collection (schema) within a database."""

    id: str
    project_id: str
    database_id: str
    name: str
    fields: list[dict[str, Any]]
    record_identifier: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not _valid_text(self.id):
            raise ValueError("Invalid collection id")
        if not _valid_text(self.project_id):
            raise ValueError("Invalid project id")
        if not _valid_text(self.database_id):
            raise ValueError("Invalid database id")
        if not _valid_text(self.name):
            raise ValueError("Invalid collection name")
        if not isinstance(self.fields, list):
            raise ValueError("Invalid fields")
        if not _valid_datetime(self.created_at):
            raise ValueError("Invalid created_at")
        if not _valid_datetime(self.updated_at):
            raise ValueError("Invalid updated_at")


@dataclass(frozen=True, slots=True)
class AppRecord:
    """A record in a collection, keyed by field ID."""

    id: str
    project_id: str
    collection_id: str
    environment: str
    values: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not _valid_text(self.id):
            raise ValueError("Invalid record id")
        if not _valid_text(self.project_id):
            raise ValueError("Invalid project id")
        if not _valid_text(self.collection_id):
            raise ValueError("Invalid collection id")
        if self.environment not in ("preview", "live"):
            raise ValueError("Invalid environment")
        if not isinstance(self.values, dict):
            raise ValueError("Invalid values")
        if not _valid_datetime(self.created_at):
            raise ValueError("Invalid created_at")
        if not _valid_datetime(self.updated_at):
            raise ValueError("Invalid updated_at")
