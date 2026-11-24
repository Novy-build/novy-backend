"""Atomic template provisioning for project creation.

Validates a template blueprint, allocates fresh IDs, and commits the project,
databases, collections, and sample records together in a single MongoDB
transaction. Rolls back everything on failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from novy.domain.projects import ProjectDraft


@dataclass(frozen=True, slots=True)
class TemplateDatabase:
    """A logical database in a template blueprint."""

    name: str
    collections: list[TemplateCollection] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TemplateCollection:
    """A collection in a template blueprint."""

    name: str
    # Alias used by the project draft before fresh IDs are allocated.
    reference_id: str = ""
    fields: list[dict[str, Any]] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    identifier: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TemplateBlueprint:
    """Validated template blueprint for atomic provisioning."""

    name: str
    project_type: str = "app"
    screens: list[dict[str, Any]] = field(default_factory=list)
    databases: list[TemplateDatabase] = field(default_factory=list)


class TemplateProvisioningError(Exception):
    """Raised when template provisioning fails."""

    def __init__(self, message: str = "Template provisioning failed") -> None:
        super().__init__(message)


class TransactionalProjectStore(Protocol):
    """Port for transactional project + data provisioning."""

    async def provision_template(self, owner_id: str, blueprint: TemplateBlueprint) -> str: ...


class TemplateProvisioningService:
    """Validates and provisions a template atomically."""

    def __init__(self, store: TransactionalProjectStore) -> None:
        self._store = store

    def validate_blueprint(self, blueprint: TemplateBlueprint) -> None:
        if not blueprint.name or not blueprint.name.strip():
            raise TemplateProvisioningError("Template name is required")
        if blueprint.project_type not in ("app", "website"):
            raise TemplateProvisioningError("Invalid project type")
        seen_references: set[str] = set()
        for db in blueprint.databases:
            if not db.name or not db.name.strip():
                raise TemplateProvisioningError("Database name is required")
            for coll in db.collections:
                if not coll.name or not coll.name.strip():
                    raise TemplateProvisioningError("Collection name is required")
                if not coll.reference_id.strip():
                    raise TemplateProvisioningError("Collection reference id is required")
                if coll.reference_id in seen_references:
                    raise TemplateProvisioningError("Duplicate collection reference id")
                seen_references.add(coll.reference_id)

    async def provision(self, owner_id: str, blueprint: TemplateBlueprint) -> str:
        self.validate_blueprint(blueprint)
        # Build draft from screens
        ProjectDraft(content={"screens": list(blueprint.screens)})
        try:
            project_id = await self._store.provision_template(owner_id, blueprint)
        except Exception as exc:
            raise TemplateProvisioningError(str(exc)) from exc
        return project_id
