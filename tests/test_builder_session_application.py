import asyncio
import traceback
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from novy.application.builder_sessions import BuilderSessionService, RevocationResult
from novy.domain.builder import BuilderAccessDenied, BuilderIdentity, BuilderProfile
from novy.domain.builder_sessions import (
    ABSOLUTE_LIFETIME,
    IDLE_LIFETIME,
    BuilderSession,
    BuilderSessionConflict,
    BuilderSessionInvalid,
    BuilderSessionStoreError,
    EncryptedTokens,
    ProviderTokens,
    SessionCredentialInvalid,
    SessionIntegrityError,
)
from novy.infrastructure.session_security import SessionSecurity

NOW = datetime(2026, 1, 1, tzinfo=UTC)
IDENTITY = BuilderIdentity("issuer", "subject")
TOKENS = ProviderTokens("synthetic-access-secret", "synthetic-refresh-secret")
NEW_TOKENS = ProviderTokens("new-access-secret", "new-refresh-secret")
PROFILE = BuilderProfile("builder", IDENTITY, "Name", "email", True, NOW, NOW)


class Profiles:
    def __init__(self) -> None:
        self.profile: BuilderProfile | None = PROFILE
        self.calls = 0
        self.error = False

    async def create(
        self, identity: BuilderIdentity, *, display_name: str, email: str
    ) -> BuilderProfile:
        raise AssertionError("Unexpected onboarding")

    async def find_by_identity(self, identity: BuilderIdentity) -> BuilderProfile | None:
        self.calls += 1
        if self.error:
            raise BuilderSessionStoreError()
        return self.profile


class Sessions:
    def __init__(self) -> None:
        self.value: BuilderSession | None = None
        self.calls: list[str] = []
        self.conflicts = 0
        self.error = ""
        self.on_conflict: Any = None
        self.output: BuilderSession | None = None
        self.output_changes: dict[str, Any] = {}
        self.now = NOW

    def check(self, operation: str) -> None:
        self.calls.append(operation)
        if self.error == operation:
            raise BuilderSessionStoreError()

    async def insert(self, session: BuilderSession) -> None:
        self.check("insert")
        self.value = session

    async def find(self, digest: bytes) -> BuilderSession | None:
        self.check("find")
        return self.value

    async def touch(self, expected: BuilderSession) -> BuilderSession | None:
        self.check("touch")
        if self.conflicts:
            self.conflicts -= 1
            if self.on_conflict:
                self.on_conflict()
            return None
        if self.value != expected:
            return None
        result = self.output or replace(
            expected,
            last_seen_at=self.now,
            idle_expires_at=min(self.now + IDLE_LIFETIME, expected.absolute_expires_at),
            revision=expected.revision + 1,
        )
        self.value = result
        return result

    async def revoke(self, expected: BuilderSession) -> BuilderSession | None:
        self.check("revoke")
        if self.conflicts:
            self.conflicts -= 1
            if self.on_conflict:
                self.on_conflict()
            return None
        if self.value != expected:
            return None
        result = self.output or replace(
            expected, revoked_at=self.now, revision=expected.revision + 1
        )
        self.value = result
        return result

    async def replace_tokens(
        self, expected: BuilderSession, encrypted: EncryptedTokens
    ) -> BuilderSession | None:
        self.check("replace_tokens")
        if self.conflicts:
            self.conflicts -= 1
            if self.on_conflict:
                self.on_conflict()
            return None
        if self.value != expected:
            return None
        changes: dict[str, Any] = {
            "tokens": encrypted,
            "revision": expected.revision + 1,
        }
        changes.update(self.output_changes)
        result = replace(expected, **changes)
        self.value = result
        return result


class Harness:
    def __init__(self) -> None:
        self.sessions = Sessions()
        self.profiles = Profiles()
        self.security = SessionSecurity(
            encryption_key=b"e" * 32, lookup_key=b"l" * 32, environment="test"
        )
        self.now = NOW
        self.service = BuilderSessionService(
            self.sessions, self.profiles, self.security, clock=lambda: self.now
        )

    def issue(self) -> str:
        return asyncio.run(self.service.issue(IDENTITY, TOKENS)).credential

    def snapshot(self) -> BuilderSession:
        assert self.sessions.value is not None
        return self.sessions.value


def test_issue_and_resolve_redaction_and_time() -> None:
    h = Harness()
    h.now = NOW.replace(microsecond=123456)
    issued = asyncio.run(h.service.issue(IDENTITY, TOKENS))
    value = h.snapshot()
    assert issued.credential not in repr(issued)
    assert issued.record_id == value.record_id
    assert value.record_id != issued.credential
    assert value.created_at == NOW.replace(microsecond=123000)
    assert value.idle_expires_at == value.created_at + IDLE_LIFETIME
    assert value.absolute_expires_at == value.created_at + ABSOLUTE_LIFETIME
    assert value.revision == 0 and value.revoked_at is None
    assert (
        h.security.decrypt(
            record_id=value.record_id,
            identity=IDENTITY,
            builder_id=PROFILE.id,
            envelope=value.tokens,
        )
        == TOKENS
    )
    h.sessions.now = value.created_at
    resolved = asyncio.run(h.service.resolve(issued.credential))
    assert resolved.record_id == value.record_id and resolved.profile == PROFILE
    assert {f.name for f in fields(resolved)} == {"record_id", "profile"}
    for secret in (issued.credential, TOKENS.access_token, TOKENS.refresh_token):
        assert secret not in repr(issued) + repr(value) + repr(resolved)
    with pytest.raises(FrozenInstanceError):
        issued.record_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "profile",
    [
        None,
        replace(PROFILE, is_active=False),
        replace(PROFILE, identity=BuilderIdentity("other", "subject")),
    ],
)
def test_issue_denies_profile_without_side_effects(profile: BuilderProfile | None) -> None:
    h = Harness()
    h.profiles.profile = profile
    with pytest.raises(BuilderAccessDenied):
        h.issue()
    assert not h.sessions.calls


def test_bad_tokens_and_clock_do_not_insert() -> None:
    h = Harness()
    with pytest.raises(ValueError, match="Invalid provider tokens"):
        asyncio.run(h.service.issue(IDENTITY, cast(ProviderTokens, "secret")))
    h.now = datetime(2026, 1, 1)
    with pytest.raises(ValueError, match="Invalid session clock"):
        h.issue()
    assert not h.sessions.calls


@pytest.mark.parametrize("kind", ["missing", "disabled", "wrong_identity", "wrong_id"])
def test_resolve_rechecks_profile(kind: str) -> None:
    h = Harness()
    credential = h.issue()
    h.profiles.profile = {
        "missing": None,
        "disabled": replace(PROFILE, is_active=False),
        "wrong_identity": replace(PROFILE, identity=BuilderIdentity("other", "subject")),
        "wrong_id": replace(PROFILE, id="other"),
    }[kind]
    with pytest.raises(BuilderSessionInvalid if kind == "wrong_id" else BuilderAccessDenied):
        asyncio.run(h.service.resolve(credential))
    assert "touch" not in h.sessions.calls


@pytest.mark.parametrize("absolute", [False, True])
def test_strict_expiry(absolute: bool) -> None:
    h = Harness()
    credential = h.issue()
    value = h.snapshot()
    if absolute:
        value = replace(
            value,
            last_seen_at=value.absolute_expires_at - timedelta(days=1),
            idle_expires_at=value.absolute_expires_at,
        )
        h.sessions.value = value
    h.now = value.absolute_expires_at if absolute else value.idle_expires_at
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.resolve(credential))
    assert "touch" not in h.sessions.calls


def test_bad_credential_and_missing_session() -> None:
    h = Harness()
    with pytest.raises(SessionCredentialInvalid) as error:
        asyncio.run(h.service.resolve("secret invalid"))
    assert "secret invalid" not in str(error.value)
    assert not h.sessions.calls
    credential = h.issue()
    h.sessions.value = None
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.resolve(credential))
    assert asyncio.run(h.service.revoke(credential)) is RevocationResult.NOT_FOUND


def test_resolve_rejects_tampered_identity_and_encryption_key_rotation() -> None:
    h = Harness()
    credential = h.issue()
    original = h.snapshot()
    other = BuilderIdentity("other-issuer", "other-subject")
    h.sessions.value = replace(original, identity=other, builder_id="other-builder")
    h.profiles.profile = replace(PROFILE, id="other-builder", identity=other)
    with pytest.raises(SessionIntegrityError):
        asyncio.run(h.service.resolve(credential))
    assert "touch" not in h.sessions.calls

    h.sessions.value = original
    h.profiles.profile = PROFILE
    rotated = SessionSecurity(encryption_key=b"n" * 32, lookup_key=b"l" * 32, environment="test")
    service = BuilderSessionService(h.sessions, h.profiles, rotated, clock=lambda: h.now)
    with pytest.raises(SessionIntegrityError):
        asyncio.run(service.resolve(credential))
    assert "touch" not in h.sessions.calls


def test_revoke_rejects_unexpected_activity_changes() -> None:
    h = Harness()
    credential = h.issue()
    later = NOW + timedelta(days=1)
    h.sessions.output = replace(
        h.snapshot(),
        last_seen_at=later,
        idle_expires_at=later + IDLE_LIFETIME,
        revoked_at=later,
        revision=1,
    )
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.revoke(credential))


@pytest.mark.parametrize("field", ["digest", "identity", "tokens", "revision"])
def test_corrupt_snapshot_fails_closed(field: str) -> None:
    h = Harness()
    credential = h.issue()
    value = h.snapshot()
    object.__setattr__(
        value,
        field,
        {"digest": b"x", "identity": "secret", "tokens": "secret", "revision": -1}[field],
    )
    with pytest.raises(BuilderSessionInvalid) as error:
        asyncio.run(h.service.resolve(credential))
    assert "secret" not in str(error.value)
    assert "touch" not in h.sessions.calls


@pytest.mark.parametrize(
    "field", ["record_id", "identity", "builder_id", "tokens", "revision", "revoked_at", "digest"]
)
def test_tampered_touch_fails_closed(field: str) -> None:
    h = Harness()
    credential = h.issue()
    value = h.snapshot()
    changes: dict[str, object] = {
        "record_id": "b" * 32,
        "identity": BuilderIdentity("other", "subject"),
        "builder_id": "other",
        "tokens": replace(value.tokens, ciphertext=b"z" * 16),
        "revision": 2,
        "revoked_at": NOW,
        "digest": b"x" * 32,
    }
    h.sessions.output = replace(value, **cast(Any, {"revision": 1, field: changes[field]}))
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.resolve(credential))


def test_touch_retry_rechecks_profile_and_exhaustion() -> None:
    h = Harness()
    credential = h.issue()
    h.sessions.conflicts = 1
    h.sessions.on_conflict = lambda: setattr(
        h.profiles, "profile", replace(PROFILE, is_active=False)
    )
    with pytest.raises(BuilderAccessDenied):
        asyncio.run(h.service.resolve(credential))
    assert h.profiles.calls == 3
    h.profiles.profile = PROFILE
    h.sessions.on_conflict = None
    h.sessions.conflicts = 3
    with pytest.raises(BuilderSessionConflict):
        asyncio.run(h.service.resolve(credential))
    assert h.sessions.calls.count("touch") == 4


def test_touch_retry_succeeds_after_fresh_profile_check() -> None:
    h = Harness()
    credential = h.issue()
    h.sessions.conflicts = 1
    resolved = asyncio.run(h.service.resolve(credential))
    assert resolved.profile == PROFILE
    assert h.profiles.calls == 3
    assert h.sessions.calls.count("find") == 2


def test_touch_checks_fresh_clock_and_rejects_regression() -> None:
    h = Harness()
    credential = h.issue()
    h.sessions.now = NOW + timedelta(days=1)
    h.now = h.sessions.now
    asyncio.run(h.service.resolve(credential))
    snapshot = h.snapshot()
    h.sessions.output = replace(
        snapshot,
        last_seen_at=NOW,
        idle_expires_at=NOW + IDLE_LIFETIME,
        revision=snapshot.revision + 1,
    )
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.resolve(credential))

    h = Harness()
    credential = h.issue()
    ticks = iter([NOW, NOW + IDLE_LIFETIME])
    h.service = BuilderSessionService(h.sessions, h.profiles, h.security, clock=lambda: next(ticks))
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.resolve(credential))


def test_revocation_race_and_repeat() -> None:
    h = Harness()
    credential = h.issue()
    h.profiles.profile = replace(PROFILE, is_active=False)
    h.sessions.conflicts = 1
    h.sessions.on_conflict = lambda: setattr(
        h.sessions, "value", replace(h.snapshot(), revision=h.snapshot().revision + 1)
    )
    assert asyncio.run(h.service.revoke(credential)) is RevocationResult.REVOKED
    assert h.snapshot().revision == 2
    assert asyncio.run(h.service.revoke(credential)) is RevocationResult.ALREADY_REVOKED
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.resolve(credential))
    assert h.profiles.calls == 1


def test_touch_loses_to_revocation() -> None:
    h = Harness()
    credential = h.issue()
    h.sessions.conflicts = 1
    h.sessions.on_conflict = lambda: setattr(
        h.sessions, "value", replace(h.snapshot(), revoked_at=NOW, revision=1)
    )
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.resolve(credential))


@pytest.mark.parametrize("field", ["record_id", "tokens", "revision", "revoked_at"])
def test_tampered_revoke_fails_closed(field: str) -> None:
    h = Harness()
    credential = h.issue()
    value = h.snapshot()
    changes: dict[str, object] = {
        "record_id": "b" * 32,
        "tokens": replace(value.tokens, ciphertext=b"z" * 16),
        "revision": 2,
        "revoked_at": None,
    }
    h.sessions.output = replace(
        value, **cast(Any, {"revision": 1, "revoked_at": NOW, field: changes[field]})
    )
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.revoke(credential))


def test_revoke_conflicts_and_disappearance() -> None:
    h = Harness()
    credential = h.issue()
    h.sessions.conflicts = 3
    with pytest.raises(BuilderSessionConflict):
        asyncio.run(h.service.revoke(credential))
    h.sessions.conflicts = 1
    h.sessions.on_conflict = lambda: setattr(h.sessions, "value", None)
    assert asyncio.run(h.service.revoke(credential)) is RevocationResult.NOT_FOUND


@pytest.mark.parametrize("operation", ["insert", "find", "touch", "revoke", "profile"])
def test_store_errors_propagate(operation: str) -> None:
    h = Harness()
    if operation == "insert":
        h.sessions.error = operation
        with pytest.raises(BuilderSessionStoreError):
            h.issue()
        assert h.sessions.value is None
        return
    credential = h.issue()
    h.sessions.error = operation
    h.profiles.error = operation == "profile"
    with pytest.raises(BuilderSessionStoreError):
        if operation == "revoke":
            asyncio.run(h.service.revoke(credential))
        else:
            asyncio.run(h.service.resolve(credential))


def test_replace_provider_tokens_rotates_only_tokens_and_revision() -> None:
    h = Harness()
    credential = h.issue()
    before = h.snapshot()
    resolved = asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))
    after = h.snapshot()

    assert resolved.record_id == before.record_id and resolved.profile == PROFILE
    assert {f.name for f in fields(resolved)} == {"record_id", "profile"}
    assert after.tokens != before.tokens
    assert after.revision == before.revision + 1
    assert after.last_seen_at == before.last_seen_at
    assert after.idle_expires_at == before.idle_expires_at
    assert after.absolute_expires_at == before.absolute_expires_at
    assert after.created_at == before.created_at
    assert after.revoked_at is None
    assert (
        h.security.decrypt(
            record_id=before.record_id,
            identity=IDENTITY,
            builder_id=PROFILE.id,
            envelope=before.tokens,
        )
        == TOKENS
    )
    assert (
        h.security.decrypt(
            record_id=after.record_id,
            identity=IDENTITY,
            builder_id=PROFILE.id,
            envelope=after.tokens,
        )
        == NEW_TOKENS
    )
    assert "touch" not in h.sessions.calls
    assert h.sessions.calls.count("replace_tokens") == 1
    for secret in (credential, NEW_TOKENS.access_token, NEW_TOKENS.refresh_token):
        assert secret not in repr(resolved) + repr(after) + repr(after.tokens)
        assert secret.encode() not in after.tokens.nonce + after.tokens.ciphertext


def test_replace_retry_refetches_and_succeeds() -> None:
    h = Harness()
    credential = h.issue()
    h.sessions.conflicts = 1
    h.sessions.on_conflict = lambda: setattr(
        h.sessions, "value", replace(h.snapshot(), revision=h.snapshot().revision + 1)
    )
    resolved = asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))
    assert resolved.record_id == h.snapshot().record_id
    assert h.snapshot().revision == 2
    assert h.sessions.calls.count("find") == 2
    assert h.sessions.calls.count("replace_tokens") == 2
    assert h.profiles.calls == 3


def test_replace_retry_rechecks_profile_and_exhaustion() -> None:
    h = Harness()
    credential = h.issue()
    h.sessions.conflicts = 1
    h.sessions.on_conflict = lambda: setattr(
        h.profiles, "profile", replace(PROFILE, is_active=False)
    )
    with pytest.raises(BuilderAccessDenied):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))
    assert h.profiles.calls == 3
    h.profiles.profile = PROFILE
    h.sessions.on_conflict = None
    h.sessions.conflicts = 3
    with pytest.raises(BuilderSessionConflict):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))
    assert h.sessions.calls.count("replace_tokens") == 4


@pytest.mark.parametrize("kind", ["missing", "disabled", "wrong_identity", "wrong_id"])
def test_replace_rechecks_profile(kind: str) -> None:
    h = Harness()
    credential = h.issue()
    h.profiles.profile = {
        "missing": None,
        "disabled": replace(PROFILE, is_active=False),
        "wrong_identity": replace(PROFILE, identity=BuilderIdentity("other", "subject")),
        "wrong_id": replace(PROFILE, id="other"),
    }[kind]
    with pytest.raises(BuilderSessionInvalid if kind == "wrong_id" else BuilderAccessDenied):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))
    assert "replace_tokens" not in h.sessions.calls


def test_replace_denies_revoked_and_expired_sessions() -> None:
    h = Harness()
    credential = h.issue()
    h.sessions.value = replace(h.snapshot(), revoked_at=NOW, revision=1)
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))
    assert "replace_tokens" not in h.sessions.calls

    h = Harness()
    credential = h.issue()
    h.now = h.snapshot().idle_expires_at
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))
    assert "replace_tokens" not in h.sessions.calls


def test_replace_rejects_tampered_envelope_and_key_rotation() -> None:
    h = Harness()
    credential = h.issue()
    original = h.snapshot()
    h.sessions.value = replace(original, tokens=replace(original.tokens, ciphertext=b"z" * 16))
    with pytest.raises(SessionIntegrityError):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))
    assert "replace_tokens" not in h.sessions.calls

    h.sessions.value = original
    rotated = SessionSecurity(encryption_key=b"n" * 32, lookup_key=b"l" * 32, environment="test")
    service = BuilderSessionService(h.sessions, h.profiles, rotated, clock=lambda: h.now)
    with pytest.raises(SessionIntegrityError):
        asyncio.run(service.replace_provider_tokens(credential, NEW_TOKENS))
    assert "replace_tokens" not in h.sessions.calls


@pytest.mark.parametrize(
    "changes",
    [
        {"record_id": "b" * 32},
        {"digest": b"x" * 32},
        {"identity": BuilderIdentity("other", "subject")},
        {"builder_id": "other"},
        {"revision": 2},
        {"revoked_at": NOW},
        {"last_seen_at": NOW + timedelta(days=1), "idle_expires_at": NOW + timedelta(days=8)},
    ],
)
def test_tampered_replacement_fails_closed(changes: dict[str, object]) -> None:
    h = Harness()
    credential = h.issue()
    h.sessions.output_changes = changes
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))


def test_replace_rejects_wrong_returned_tokens_and_lifetime() -> None:
    h = Harness()
    credential = h.issue()
    snapshot = h.snapshot()
    different = h.security.encrypt(
        record_id=snapshot.record_id,
        identity=IDENTITY,
        builder_id=PROFILE.id,
        tokens=TOKENS,
    )
    h.sessions.output_changes = {"tokens": different}
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))

    h = Harness()
    credential = h.issue()
    snapshot = h.snapshot()
    shift = timedelta(seconds=1)
    h.sessions.output_changes = {
        "created_at": snapshot.created_at + shift,
        "last_seen_at": snapshot.last_seen_at + shift,
        "idle_expires_at": snapshot.idle_expires_at + shift,
        "absolute_expires_at": snapshot.absolute_expires_at + shift,
    }
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))


def test_replace_checks_fresh_clock() -> None:
    h = Harness()
    credential = h.issue()
    ticks = iter([NOW, NOW + IDLE_LIFETIME])
    h.service = BuilderSessionService(h.sessions, h.profiles, h.security, clock=lambda: next(ticks))
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))


def test_replace_rejects_bad_tokens_before_writes() -> None:
    h = Harness()
    credential = h.issue()
    with pytest.raises(ValueError, match="Invalid provider tokens"):
        asyncio.run(h.service.replace_provider_tokens(credential, cast(ProviderTokens, "secret")))
    assert h.sessions.calls == ["insert"]


@pytest.mark.parametrize("operation", ["find", "replace_tokens", "profile"])
def test_replace_store_errors_propagate(operation: str) -> None:
    h = Harness()
    credential = h.issue()
    h.sessions.error = operation
    h.profiles.error = operation == "profile"
    with pytest.raises(BuilderSessionStoreError):
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))


@pytest.mark.parametrize("kind", ["conflict", "tampered", "integrity", "revoked", "store"])
def test_replace_errors_never_leak_secrets(kind: str) -> None:
    h = Harness()
    credential = h.issue()
    if kind == "conflict":
        h.sessions.conflicts = 3
        expected_error: type[Exception] = BuilderSessionConflict
    elif kind == "tampered":
        h.sessions.output_changes = {"builder_id": "other"}
        expected_error = BuilderSessionInvalid
    elif kind == "integrity":
        h.sessions.value = replace(
            h.snapshot(), tokens=replace(h.snapshot().tokens, ciphertext=b"z" * 16)
        )
        expected_error = SessionIntegrityError
    elif kind == "revoked":
        h.sessions.value = replace(h.snapshot(), revoked_at=NOW, revision=1)
        expected_error = BuilderSessionInvalid
    else:
        h.sessions.error = "replace_tokens"
        expected_error = BuilderSessionStoreError
    with pytest.raises(expected_error) as error:
        asyncio.run(h.service.replace_provider_tokens(credential, NEW_TOKENS))
    for secret in (credential, NEW_TOKENS.access_token, NEW_TOKENS.refresh_token):
        assert secret not in str(error.value)
        assert secret not in "".join(
            traceback.format_exception(type(error.value), error.value, error.value.__traceback__)
        )
