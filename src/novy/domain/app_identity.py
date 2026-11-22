"""Generated-app identity and membership domain models.

Separate from builder identity. An app-user identity is shared across
projects (same password/recovery), but membership is per-project and
per-environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from novy.domain.projects import _valid_datetime, _valid_text


class AppIdentityError(Exception):
    """Raised when app identity data is invalid."""

    def __init__(self, message: str = "Invalid app identity") -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AppUserIdentity:
    """A generated-app user identity (shared realm)."""

    id: str
    issuer: str
    subject: str
    display_name: str
    email: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not _valid_text(self.id):
            raise ValueError("Invalid identity id")
        if not _valid_text(self.issuer):
            raise ValueError("Invalid issuer")
        if not _valid_text(self.subject):
            raise ValueError("Invalid subject")
        if not _valid_text(self.display_name):
            raise ValueError("Invalid display name")
        if not _valid_text(self.email):
            raise ValueError("Invalid email")
        if type(self.is_active) is not bool:
            raise ValueError("Invalid is_active")
        if not _valid_datetime(self.created_at):
            raise ValueError("Invalid created_at")
        if not _valid_datetime(self.updated_at):
            raise ValueError("Invalid updated_at")


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    """Per-project, per-environment membership for an app user."""

    id: str
    project_id: str
    environment: str
    identity_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not _valid_text(self.id):
            raise ValueError("Invalid membership id")
        if not _valid_text(self.project_id):
            raise ValueError("Invalid project id")
        if self.environment not in ("preview", "live"):
            raise ValueError("Invalid environment")
        if not _valid_text(self.identity_id):
            raise ValueError("Invalid identity id")
        if type(self.is_active) is not bool:
            raise ValueError("Invalid is_active")
        if not _valid_datetime(self.created_at):
            raise ValueError("Invalid created_at")
        if not _valid_datetime(self.updated_at):
            raise ValueError("Invalid updated_at")
