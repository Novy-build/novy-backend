"""Builder session snapshots, independent of authentication and persistence."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from novy.domain.builder import BuilderIdentity

IDLE_LIFETIME = timedelta(days=7)
ABSOLUTE_LIFETIME = timedelta(days=30)


class SessionCredentialInvalid(Exception):
    def __init__(self) -> None:
        super().__init__("Invalid session credential")


class SessionIntegrityError(Exception):
    def __init__(self) -> None:
        super().__init__("Invalid session integrity")


class BuilderSessionInvalid(Exception):
    def __init__(self) -> None:
        super().__init__("Invalid builder session")


class BuilderSessionStoreError(Exception):
    def __init__(self) -> None:
        super().__init__("Builder session store error")


class BuilderSessionConflict(Exception):
    def __init__(self) -> None:
        super().__init__("Builder session conflict")


@dataclass(frozen=True, slots=True, repr=False)
class ProviderTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)

    def __post_init__(self) -> None:
        for token in (self.access_token, self.refresh_token):
            if not isinstance(token, str) or not token.strip():
                raise ValueError("Invalid provider tokens")
            try:
                valid = len(token.encode("utf-8")) <= 16384
            except UnicodeEncodeError:
                valid = False
            if not valid:
                raise ValueError("Invalid provider tokens")


@dataclass(frozen=True, slots=True)
class EncryptedTokens:
    key_id: str
    nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)
    version: int = 1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,32}", self.key_id) is None
            or type(self.nonce) is not bytes
            or len(self.nonce) != 12
            or type(self.ciphertext) is not bytes
            or not 16 <= len(self.ciphertext) <= 40000
            or type(self.version) is not int
            or self.version != 1
        ):
            raise ValueError("Invalid encrypted tokens")


def _utc_time(value: object, *, aligned: bool) -> datetime:
    try:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        result = value.astimezone(UTC)
        if aligned and result.microsecond % 1000:
            raise ValueError
        return result
    except (ValueError, OverflowError, TypeError):
        raise ValueError("Invalid session timestamp") from None


@dataclass(frozen=True, slots=True)
class BuilderSession:
    record_id: str
    digest: bytes = field(repr=False)
    identity: BuilderIdentity
    builder_id: str
    tokens: EncryptedTokens = field(repr=False)
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None
    revision: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.record_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", self.record_id) is None
            or type(self.digest) is not bytes
            or len(self.digest) != 32
            or not isinstance(self.identity, BuilderIdentity)
            or not isinstance(self.builder_id, str)
            or not self.builder_id.strip()
            or not isinstance(self.tokens, EncryptedTokens)
            or type(self.revision) is not int
            or self.revision < 0
            or type(self.schema_version) is not int
            or self.schema_version != 1
        ):
            raise ValueError("Invalid builder session")

        for name in ("created_at", "last_seen_at", "idle_expires_at", "absolute_expires_at"):
            object.__setattr__(self, name, _utc_time(getattr(self, name), aligned=True))
        if self.revoked_at is not None:
            object.__setattr__(self, "revoked_at", _utc_time(self.revoked_at, aligned=True))

        try:
            valid = (
                self.absolute_expires_at == self.created_at + ABSOLUTE_LIFETIME
                and self.created_at <= self.last_seen_at <= self.absolute_expires_at
                and self.idle_expires_at
                == (
                    self.absolute_expires_at
                    if self.last_seen_at >= self.absolute_expires_at - IDLE_LIFETIME
                    else self.last_seen_at + IDLE_LIFETIME
                )
                and (self.revoked_at is None or self.revoked_at >= self.created_at)
            )
        except OverflowError:
            raise ValueError("Invalid builder session") from None
        if not valid:
            raise ValueError("Invalid builder session")

    def is_valid_at(self, now: datetime) -> bool:
        now = _utc_time(now, aligned=False)
        return (
            self.revoked_at is None
            and now >= self.created_at
            and now < self.idle_expires_at
            and now < self.absolute_expires_at
        )
