"""Project domain models and rules, independent of FastAPI, MongoDB, and HTTP DTOs.

Framework-independent entities: Project, ProjectDraft, ProjectRevision,
ProjectLifecycleState, and ProjectType. All validation is performed here;
callers must not rely on transport-layer checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_TYPE_APP = "app"
PROJECT_TYPE_WEBSITE = "website"
SUPPORTED_PROJECT_TYPES = (PROJECT_TYPE_APP, PROJECT_TYPE_WEBSITE)
DEFAULT_PROJECT_TYPE = PROJECT_TYPE_APP

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_DELETED = "deleted"
SUPPORTED_LIFECYCLE_STATES = (LIFECYCLE_ACTIVE, LIFECYCLE_DELETED)

SUPPORTED_SCHEMA_VERSION = 14


def _valid_text(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valid_datetime(value: object) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProjectDataError(Exception):
    """Raised when project data fails domain validation."""

    def __init__(self) -> None:
        super().__init__("Invalid project data")


class ProjectConflictError(Exception):
    """Raised when a save conflicts with the current revision (stale_revision)."""

    def __init__(self) -> None:
        super().__init__("Project revision conflict")


class ProjectPreconditionRequiredError(Exception):
    """Raised when a required revision precondition (If-Match) is missing."""

    def __init__(self) -> None:
        super().__init__("Project revision precondition required")


class ProjectNotFoundError(Exception):
    """Raised when a project cannot be found for the given owner."""

    def __init__(self) -> None:
        super().__init__("Project not found")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProjectRevision:
    """Independent monotonically changing save/concurrency token.

    Distinct from ``schemaVersion``. Incremented on every successful save.
    """

    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool):
            raise ValueError("Invalid project revision")
        if self.value < 0:
            raise ValueError("Invalid project revision")


@dataclass(frozen=True, slots=True)
class ProjectDraft:
    """Structurally safe but potentially semantically incomplete project content.

    Retains unknown supported draft content, responsive layouts, routes,
    behavior graphs, data bindings, and embedded IDs. Does not enforce
    complete project semantics.
    """

    content: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.content, dict):
            raise ValueError("Invalid project draft")


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Project:
    """Owner-scoped project aggregate.

    ``schemaVersion`` describes the project format; ``revision`` is an
    independent save/concurrency token. ``projectType`` is project metadata,
    not document content.
    """

    id: str
    owner_id: str
    name: str
    created_at: datetime
    updated_at: datetime
    draft: ProjectDraft
    schema_version: int
    revision: ProjectRevision
    lifecycle_state: str
    project_type: str = DEFAULT_PROJECT_TYPE

    def __post_init__(self) -> None:
        if not _valid_text(self.id):
            raise ValueError("Invalid project id")
        if not _valid_text(self.owner_id):
            raise ValueError("Invalid project owner")
        if not _valid_text(self.name):
            raise ValueError("Invalid project name")
        if not _valid_datetime(self.created_at):
            raise ValueError("Invalid project created_at")
        if not _valid_datetime(self.updated_at):
            raise ValueError("Invalid project updated_at")
        if not isinstance(self.draft, ProjectDraft):
            raise ValueError("Invalid project draft")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ValueError("Invalid project schema_version")
        if self.schema_version < 1:
            raise ValueError("Invalid project schema_version")
        if not isinstance(self.revision, ProjectRevision):
            raise ValueError("Invalid project revision")
        if self.lifecycle_state not in SUPPORTED_LIFECYCLE_STATES:
            raise ValueError("Invalid project lifecycle state")
        if self.project_type not in SUPPORTED_PROJECT_TYPES:
            raise ValueError("Invalid project type")


def is_active(project: Project) -> bool:
    """Return True when the project is in the active lifecycle state."""
    return project.lifecycle_state == LIFECYCLE_ACTIVE


def is_deleted(project: Project) -> bool:
    """Return True when the project is tombstoned."""
    return project.lifecycle_state == LIFECYCLE_DELETED
