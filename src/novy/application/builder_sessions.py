"""Internal builder session orchestration; callers must verify identity and onboarding.

Profile checks cannot be atomic with a subsequent session operation.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from novy.application.builder_profiles import (
    BuilderProfileRepository,
    require_active_builder_profile,
)
from novy.domain.builder import BuilderIdentity, BuilderProfile
from novy.domain.builder_sessions import (
    ABSOLUTE_LIFETIME,
    IDLE_LIFETIME,
    BuilderSession,
    BuilderSessionConflict,
    BuilderSessionInvalid,
    EncryptedTokens,
    ProviderTokens,
)


class BuilderSessionRepository(Protocol):
    async def insert(self, session: BuilderSession) -> None: ...

    async def find(self, digest: bytes) -> BuilderSession | None: ...

    async def touch(self, expected: BuilderSession) -> BuilderSession | None: ...

    async def revoke(self, expected: BuilderSession) -> BuilderSession | None: ...


class SessionProtector(Protocol):
    def generate_credential(self) -> str: ...

    def digest(self, credential: str) -> bytes: ...

    def encrypt(
        self, *, record_id: str, identity: BuilderIdentity, builder_id: str, tokens: ProviderTokens
    ) -> EncryptedTokens: ...

    def decrypt(
        self,
        *,
        record_id: str,
        identity: BuilderIdentity,
        builder_id: str,
        envelope: EncryptedTokens,
    ) -> ProviderTokens: ...


@dataclass(frozen=True, slots=True)
class IssuedBuilderSession:
    credential: str = field(repr=False)
    record_id: str


@dataclass(frozen=True, slots=True)
class ResolvedBuilderSession:
    record_id: str
    profile: BuilderProfile


class RevocationResult(StrEnum):
    REVOKED = "revoked"
    ALREADY_REVOKED = "already_revoked"
    NOT_FOUND = "not_found"


def _validate(session: BuilderSession, digest: bytes) -> None:
    try:
        if not isinstance(session, BuilderSession) or session.digest != digest:
            raise ValueError
        BuilderIdentity(session.identity.issuer, session.identity.subject)
        EncryptedTokens(
            session.tokens.key_id,
            session.tokens.nonce,
            session.tokens.ciphertext,
            session.tokens.version,
        )
        replace(session)
    except (ValueError, TypeError, AttributeError, OverflowError):
        pass
    else:
        return
    raise BuilderSessionInvalid() from None


def _same_record(before: BuilderSession, after: BuilderSession) -> bool:
    return (
        before.record_id == after.record_id
        and before.digest == after.digest
        and before.identity == after.identity
        and before.builder_id == after.builder_id
        and before.tokens == after.tokens
        and before.created_at == after.created_at
        and before.absolute_expires_at == after.absolute_expires_at
        and before.schema_version == after.schema_version
        and after.revision == before.revision + 1
    )


class BuilderSessionService:
    def __init__(
        self,
        sessions: BuilderSessionRepository,
        profiles: BuilderProfileRepository,
        protector: SessionProtector,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._profiles = profiles
        self._protector = protector
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def _now(self) -> datetime:
        try:
            now = self._clock()
            if not isinstance(now, datetime) or now.utcoffset() is None:
                raise ValueError
            now = now.astimezone(UTC)
            return now.replace(microsecond=now.microsecond // 1000 * 1000)
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Invalid session clock") from None

    async def issue(
        self, identity: BuilderIdentity, tokens: ProviderTokens
    ) -> IssuedBuilderSession:
        """Issue only for an already verified, explicitly onboarded builder."""
        profile = await require_active_builder_profile(self._profiles, identity)
        if not isinstance(tokens, ProviderTokens):
            raise ValueError("Invalid provider tokens")
        ProviderTokens(tokens.access_token, tokens.refresh_token)
        now = self._now()
        record_id = secrets.token_hex(16)
        credential = self._protector.generate_credential()
        digest = self._protector.digest(credential)
        encrypted = self._protector.encrypt(
            record_id=record_id, identity=identity, builder_id=profile.id, tokens=tokens
        )
        session = BuilderSession(
            record_id=record_id,
            digest=digest,
            identity=identity,
            builder_id=profile.id,
            tokens=encrypted,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now + IDLE_LIFETIME,
            absolute_expires_at=now + ABSOLUTE_LIFETIME,
            revoked_at=None,
            revision=0,
        )
        _validate(session, digest)
        await self._sessions.insert(session)
        return IssuedBuilderSession(credential=credential, record_id=record_id)

    async def resolve(self, credential: str) -> ResolvedBuilderSession:
        digest = self._protector.digest(credential)
        for _ in range(3):
            snapshot = await self._sessions.find(digest)
            if snapshot is None:
                raise BuilderSessionInvalid()
            _validate(snapshot, digest)
            if not snapshot.is_valid_at(self._now()):
                raise BuilderSessionInvalid()
            self._protector.decrypt(
                record_id=snapshot.record_id,
                identity=snapshot.identity,
                builder_id=snapshot.builder_id,
                envelope=snapshot.tokens,
            )
            profile = await require_active_builder_profile(self._profiles, snapshot.identity)
            if profile.id != snapshot.builder_id:
                raise BuilderSessionInvalid()
            touched = await self._sessions.touch(snapshot)
            if touched is None:
                continue
            _validate(touched, digest)
            if (
                not _same_record(snapshot, touched)
                or touched.last_seen_at < snapshot.last_seen_at
                or touched.idle_expires_at < snapshot.idle_expires_at
                or not touched.is_valid_at(self._now())
            ):
                raise BuilderSessionInvalid()
            return ResolvedBuilderSession(record_id=touched.record_id, profile=profile)
        raise BuilderSessionConflict()

    async def revoke(self, credential: str) -> RevocationResult:
        digest = self._protector.digest(credential)
        for _ in range(3):
            snapshot = await self._sessions.find(digest)
            if snapshot is None:
                return RevocationResult.NOT_FOUND
            _validate(snapshot, digest)
            if snapshot.revoked_at is not None:
                return RevocationResult.ALREADY_REVOKED
            revoked = await self._sessions.revoke(snapshot)
            if revoked is None:
                continue
            _validate(revoked, digest)
            if (
                not _same_record(snapshot, revoked)
                or revoked.revoked_at is None
                or revoked.last_seen_at != snapshot.last_seen_at
                or revoked.idle_expires_at != snapshot.idle_expires_at
            ):
                raise BuilderSessionInvalid()
            return RevocationResult.REVOKED
        raise BuilderSessionConflict()
