"""Builder profile data, independent of identity verification and access decisions.

Callers must provide an already verified builder-realm identity. Creating a
profile is onboarding and must be explicitly authorized by the caller.
"""

from dataclasses import dataclass
from datetime import datetime


def _valid_text(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class BuilderIdentity:
    """An exact identity reference, not proof of verification or builder access."""

    issuer: str
    subject: str

    def __post_init__(self) -> None:
        for value in (self.issuer, self.subject):
            if not _valid_text(value):
                raise ValueError("Invalid builder identity")


@dataclass(frozen=True, slots=True)
class BuilderProfile:
    id: str
    identity: BuilderIdentity
    display_name: str
    email: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for value in (self.id, self.display_name, self.email):
            if not _valid_text(value):
                raise ValueError("Invalid builder profile")
        if not isinstance(self.identity, BuilderIdentity) or type(self.is_active) is not bool:
            raise ValueError("Invalid builder profile")
        for timestamp in (self.created_at, self.updated_at):
            if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
                raise ValueError("Invalid builder profile")


class BuilderProfileAlreadyExists(Exception):
    def __init__(self) -> None:
        super().__init__("Builder profile already exists")


class BuilderProfileDataError(Exception):
    def __init__(self) -> None:
        super().__init__("Invalid builder profile data")


class BuilderAccessDenied(Exception):
    def __init__(self) -> None:
        super().__init__("Builder access denied")
