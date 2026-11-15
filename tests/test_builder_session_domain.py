from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest

from novy.domain.builder import BuilderIdentity
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

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MARKER = "synthetic-sensitive-marker"
IDENTITY = BuilderIdentity("synthetic-issuer", "synthetic-subject")


def session() -> BuilderSession:
    return BuilderSession(
        record_id="a" * 32,
        digest=b"d" * 32,
        identity=IDENTITY,
        builder_id="synthetic-builder",
        tokens=EncryptedTokens("key_1", b"n" * 12, b"c" * 16),
        created_at=NOW,
        last_seen_at=NOW,
        idle_expires_at=NOW + IDLE_LIFETIME,
        absolute_expires_at=NOW + ABSOLUTE_LIFETIME,
        revoked_at=None,
        revision=0,
    )


def test_expiry_boundaries_and_revocation() -> None:
    value = session()
    assert not value.is_valid_at(value.created_at - timedelta(microseconds=1))
    assert value.is_valid_at(value.idle_expires_at - timedelta(microseconds=1))
    assert not value.is_valid_at(value.idle_expires_at)
    assert not value.is_valid_at(value.idle_expires_at + timedelta(microseconds=1))
    assert not replace(value, revoked_at=NOW).is_valid_at(NOW)
    capped = replace(
        value,
        last_seen_at=value.absolute_expires_at,
        idle_expires_at=value.absolute_expires_at,
        revision=1,
    )
    assert capped.is_valid_at(capped.absolute_expires_at - timedelta(microseconds=1))
    assert not capped.is_valid_at(capped.absolute_expires_at)


def test_touch_snapshot_and_absolute_cap() -> None:
    value = session()
    touched_at = NOW + timedelta(days=5)
    touched = replace(
        value, last_seen_at=touched_at, idle_expires_at=touched_at + IDLE_LIFETIME, revision=1
    )
    assert touched.is_valid_at(NOW + timedelta(days=8))
    late = NOW + timedelta(days=28)
    capped = replace(touched, last_seen_at=late, idle_expires_at=value.absolute_expires_at)
    assert capped.idle_expires_at == NOW + ABSOLUTE_LIFETIME
    with pytest.raises(ValueError):
        replace(capped, idle_expires_at=late + IDLE_LIFETIME)


def test_absolute_cap_near_datetime_max() -> None:
    deadline = datetime.max.replace(tzinfo=UTC, microsecond=999000)
    created = deadline - ABSOLUTE_LIFETIME
    value = replace(
        session(),
        created_at=created,
        last_seen_at=deadline,
        idle_expires_at=deadline,
        absolute_expires_at=deadline,
    )
    assert not value.is_valid_at(deadline)


def test_timestamp_normalization() -> None:
    offset = timezone(timedelta(hours=2))
    value = session()
    converted = replace(
        value,
        created_at=value.created_at.astimezone(offset),
        last_seen_at=value.last_seen_at.astimezone(offset),
        idle_expires_at=value.idle_expires_at.astimezone(offset),
        absolute_expires_at=value.absolute_expires_at.astimezone(offset),
        revoked_at=value.created_at.astimezone(offset),
    )
    assert converted.created_at.tzinfo is UTC
    assert converted.revoked_at is not None and converted.revoked_at.tzinfo is UTC
    assert converted.created_at == NOW


@pytest.mark.parametrize(
    "name", ["created_at", "last_seen_at", "idle_expires_at", "absolute_expires_at", "revoked_at"]
)
@pytest.mark.parametrize("value", [datetime(2026, 1, 1), NOW.replace(microsecond=1), MARKER, 1])
def test_invalid_timestamps(name: str, value: object) -> None:
    with pytest.raises(ValueError) as error:
        replace(session(), **cast(Any, {name: value}))
    assert MARKER not in str(error.value)


@pytest.mark.parametrize("value", [None, datetime(2026, 1, 1), MARKER, True])
def test_invalid_now(value: object) -> None:
    with pytest.raises(ValueError) as error:
        session().is_valid_at(cast(datetime, value))
    assert MARKER not in str(error.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("created_at", NOW + timedelta(days=1)),
        ("last_seen_at", NOW - timedelta(milliseconds=1)),
        ("last_seen_at", NOW + timedelta(days=31)),
        ("idle_expires_at", NOW + timedelta(days=6)),
        ("absolute_expires_at", NOW + timedelta(days=31)),
        ("revoked_at", NOW - timedelta(milliseconds=1)),
        ("record_id", "A" * 32),
        ("record_id", "a" * 31),
        ("record_id", "g" * 32),
        ("digest", b"d" * 31),
        ("digest", bytearray(32)),
        ("identity", MARKER),
        ("builder_id", "  "),
        ("builder_id", None),
        ("tokens", MARKER),
        ("revision", True),
        ("revision", -1),
        ("revision", 1.0),
        ("schema_version", True),
        ("schema_version", 2),
    ],
)
def test_invalid_session_fields(name: str, value: object) -> None:
    with pytest.raises(ValueError) as error:
        replace(session(), **cast(Any, {name: value}))
    assert MARKER not in str(error.value)
    assert MARKER not in repr(error.value)


@pytest.mark.parametrize("name", ["access_token", "refresh_token"])
@pytest.mark.parametrize("value", ["", "   ", "\ud800", "x" * 16385, "\u00e9" * 8193, None, 1])
def test_invalid_provider_tokens(name: str, value: object) -> None:
    values: dict[str, object] = {"access_token": MARKER, "refresh_token": MARKER}
    values[name] = value
    with pytest.raises(ValueError) as error:
        ProviderTokens(**cast(Any, values))
    assert MARKER not in str(error.value)
    assert MARKER not in repr(error.value)


def test_provider_token_byte_limit_and_repr() -> None:
    tokens = ProviderTokens("\u00e9" * 8192, MARKER)
    assert MARKER not in repr(tokens)
    assert "access_token" not in repr(tokens)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("key_id", ""),
        ("key_id", "x" * 33),
        ("key_id", "a.b"),
        ("key_id", "\u00e9"),
        ("nonce", b"n" * 11),
        ("nonce", bytearray(12)),
        ("ciphertext", b"c" * 15),
        ("ciphertext", b"c" * 40001),
        ("ciphertext", MARKER),
        ("version", True),
        ("version", 1.0),
        ("version", 2),
    ],
)
def test_invalid_envelope(name: str, value: object) -> None:
    envelope = EncryptedTokens("key", b"n" * 12, b"c" * 16)
    with pytest.raises(ValueError) as error:
        replace(envelope, **cast(Any, {name: value}))
    assert MARKER not in str(error.value)
    assert MARKER not in repr(error.value)


def test_envelope_limits_and_sensitive_repr() -> None:
    envelope = EncryptedTokens("a" * 32, b"n" * 12, b"c" * 40000)
    assert len(envelope.ciphertext) == 40000


def test_safe_repr_and_immutability() -> None:
    envelope = EncryptedTokens("key", b"n" * 12, MARKER.encode())
    value = replace(session(), digest=MARKER.encode().ljust(32, b"x"), tokens=envelope)
    assert MARKER not in repr(envelope)
    assert MARKER not in repr(value)
    for item, name in (
        (ProviderTokens(MARKER, MARKER), "access_token"),
        (envelope, "version"),
        (value, "revision"),
    ):
        assert not hasattr(item, "__dict__")
        with pytest.raises(FrozenInstanceError):
            item.__setattr__(name, 2)


@pytest.mark.parametrize(
    "exception",
    [
        SessionCredentialInvalid,
        SessionIntegrityError,
        BuilderSessionInvalid,
        BuilderSessionStoreError,
        BuilderSessionConflict,
    ],
)
def test_exceptions_have_fixed_safe_messages(exception: type[Exception]) -> None:
    error = exception()
    assert str(error)
    assert MARKER not in str(error)
    assert MARKER not in repr(error)
