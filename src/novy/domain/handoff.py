"""Narrow builder handoff grant domain models.

A handoff grant is short-lived, single-use, and bound to a target project,
product environment, builder, and intended runtime origin. The builder's
broad session never reaches the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from novy.domain.projects import _valid_datetime, _valid_text


class HandoffError(Exception):
    """Raised when a handoff operation fails."""

    def __init__(self, message: str = "Handoff failed") -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class HandoffGrant:
    """A single-use handoff grant."""

    id: str
    project_id: str
    environment: str
    builder_id: str
    runtime_origin: str
    grant_hash: str
    created_at: datetime
    expires_at: datetime
    consumed: bool = False

    def __post_init__(self) -> None:
        if not _valid_text(self.id):
            raise ValueError("Invalid grant id")
        if not _valid_text(self.project_id):
            raise ValueError("Invalid project id")
        if self.environment not in ("preview", "live"):
            raise ValueError("Invalid environment")
        if not _valid_text(self.builder_id):
            raise ValueError("Invalid builder id")
        if not _valid_text(self.runtime_origin):
            raise ValueError("Invalid runtime origin")
        if not _valid_text(self.grant_hash):
            raise ValueError("Invalid grant hash")
        if not _valid_datetime(self.created_at):
            raise ValueError("Invalid created_at")
        if not _valid_datetime(self.expires_at):
            raise ValueError("Invalid expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("Grant must expire after creation")

    def is_expired(self, now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(UTC)
        return now >= self.expires_at

    def is_consumable(self, now: datetime | None = None) -> bool:
        return not self.consumed and not self.is_expired(now)
