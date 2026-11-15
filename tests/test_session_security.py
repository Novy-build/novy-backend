import base64
import hashlib
import hmac
import os
import traceback
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from novy.domain.builder import BuilderIdentity
from novy.domain.builder_sessions import (
    EncryptedTokens,
    ProviderTokens,
    SessionCredentialInvalid,
    SessionIntegrityError,
)
from novy.infrastructure.session_security import SessionSecurity, _aad

IDENTITY = BuilderIdentity("https://synthetic.example.test", "synthetic-subject")
TOKENS = ProviderTokens("synthetic-access-secret", "synthetic-refresh-secret")
CONTEXT = {"record_id": "record-1", "identity": IDENTITY, "builder_id": "builder-1"}


@pytest.fixture
def keys() -> tuple[bytes, bytes]:
    return os.urandom(32), os.urandom(32)


@pytest.fixture
def security(keys: tuple[bytes, bytes]) -> SessionSecurity:
    return SessionSecurity(encryption_key=keys[0], lookup_key=keys[1], environment="test")


@pytest.mark.parametrize("invalid", [b"", b"x" * 31, b"x" * 33, "x" * 32, None, bytearray(32)])
@pytest.mark.parametrize("field", ["encryption_key", "lookup_key"])
def test_invalid_keys_are_rejected(keys: tuple[bytes, bytes], field: str, invalid: object) -> None:
    args: dict[str, Any] = {"encryption_key": keys[0], "lookup_key": keys[1], "environment": "test"}
    args[field] = invalid
    with pytest.raises(ValueError, match="Invalid session security configuration") as error:
        SessionSecurity(**args)
    assert error.value.__context__ is None
    assert repr(keys[0]) not in str(error.value)


def test_configuration_and_repr(keys: tuple[bytes, bytes], security: SessionSecurity) -> None:
    with pytest.raises(ValueError):
        SessionSecurity(encryption_key=keys[0], lookup_key=keys[0], environment="test")
    for invalid in ("", "TEST", "staging", None, 1):
        with pytest.raises(ValueError):
            SessionSecurity(
                encryption_key=keys[0], lookup_key=keys[1], environment=cast(str, invalid)
            )
    for env in ("development", "test", "production"):
        assert SessionSecurity(encryption_key=keys[0], lookup_key=keys[1], environment=env)
    assert repr(keys[0]) not in repr(security)
    assert repr(keys[1]) not in repr(security)


def test_credentials_and_digest(security: SessionSecurity, keys: tuple[bytes, bytes]) -> None:
    credentials = {security.generate_credential() for _ in range(256)}
    assert len(credentials) == 256
    for credential in credentials:
        raw = base64.urlsafe_b64decode(credential + "=")
        assert len(credential) == 43 and len(raw) == 32
        assert base64.urlsafe_b64encode(raw).decode().rstrip("=") == credential
        expected = hmac.digest(keys[1], b"novy:builder-session:lookup:v1\0" + raw, hashlib.sha256)
        assert security.digest(credential) == expected == security.digest(credential)
        assert expected != hmac.digest(keys[1], raw, hashlib.sha256)


@pytest.mark.parametrize(
    "credential",
    [
        None,
        b"a" * 43,
        "",
        "a" * 42,
        "a" * 44,
        "a" * 100000,
        "a" * 42 + "=",
        "a" * 42 + "+",
        "a" * 42 + "/",
        "a" * 42 + "\ud800",
        "a" * 42 + "B",
    ],
)
def test_invalid_credentials(security: SessionSecurity, credential: object) -> None:
    with pytest.raises(SessionCredentialInvalid) as error:
        security.digest(cast(str, credential))
    assert error.value.__context__ is None
    assert "a" * 42 not in str(error.value)


def test_roundtrip_and_fresh_nonce(security: SessionSecurity) -> None:
    first = security.encrypt(**CONTEXT, tokens=TOKENS)  # type: ignore[arg-type]
    second = security.encrypt(**CONTEXT, tokens=TOKENS)  # type: ignore[arg-type]
    assert first.key_id == "v1" and first.version == 1
    assert len(first.nonce) == 12 and first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert TOKENS.access_token.encode() not in first.ciphertext
    assert TOKENS.refresh_token.encode() not in first.ciphertext
    assert security.decrypt(**CONTEXT, envelope=first) == TOKENS  # type: ignore[arg-type]


def test_unicode_tokens_roundtrip(security: SessionSecurity) -> None:
    tokens = ProviderTokens("synthetic-\u00e9-access", "synthetic-\u6f22-refresh")
    envelope = security.encrypt(**CONTEXT, tokens=tokens)  # type: ignore[arg-type]
    assert security.decrypt(**CONTEXT, envelope=envelope) == tokens  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["", "   ", "\ud800", "x" * 16385, None, 123])
def test_invalid_tokens_and_context_are_rejected_without_context(
    security: SessionSecurity, value: object
) -> None:
    tokens = ProviderTokens("synthetic-access-secret", "synthetic-refresh-secret")
    object.__setattr__(tokens, "access_token", value)
    with pytest.raises(SessionIntegrityError) as error:
        security.encrypt(**CONTEXT, tokens=tokens)  # type: ignore[arg-type]
    assert error.value.__context__ is None
    assert "synthetic-refresh-secret" not in "".join(traceback.format_exception(error.value))
    with pytest.raises(SessionIntegrityError) as error:
        security.encrypt(
            record_id=cast(str, value), identity=IDENTITY, builder_id="b", tokens=TOKENS
        )
    assert error.value.__context__ is None


def test_authentication_binds_every_context_and_envelope_field(
    security: SessionSecurity, keys: tuple[bytes, bytes]
) -> None:
    envelope = security.encrypt(**CONTEXT, tokens=TOKENS)  # type: ignore[arg-type]
    contexts = [
        {**CONTEXT, "record_id": "record-2"},
        {**CONTEXT, "builder_id": "builder-2"},
        {**CONTEXT, "identity": BuilderIdentity("https://other.example.test", IDENTITY.subject)},
        {**CONTEXT, "identity": BuilderIdentity(IDENTITY.issuer, "other-subject")},
    ]
    for context in contexts:
        with pytest.raises(SessionIntegrityError) as error:
            security.decrypt(**context, envelope=envelope)  # type: ignore[arg-type]
        assert error.value.__context__ is None
    for other in (
        SessionSecurity(encryption_key=keys[0], lookup_key=keys[1], environment="production"),
        SessionSecurity(encryption_key=os.urandom(32), lookup_key=keys[1], environment="test"),
    ):
        with pytest.raises(SessionIntegrityError):
            other.decrypt(**CONTEXT, envelope=envelope)  # type: ignore[arg-type]
    for changed in (
        {"nonce": bytes([envelope.nonce[0] ^ 1]) + envelope.nonce[1:]},
        {"ciphertext": bytes([envelope.ciphertext[0] ^ 1]) + envelope.ciphertext[1:]},
        {"key_id": "v2"},
        {"version": 2},
        {"version": True},
        {"nonce": b"short"},
        {"ciphertext": b"short"},
    ):
        corrupted = EncryptedTokens(
            key_id=envelope.key_id, nonce=envelope.nonce, ciphertext=envelope.ciphertext
        )
        for field, value in changed.items():
            object.__setattr__(corrupted, field, value)
        with pytest.raises(SessionIntegrityError) as error:
            security.decrypt(**CONTEXT, envelope=corrupted)  # type: ignore[arg-type]
        assert error.value.__context__ is None
        rendered = "".join(traceback.format_exception(error.value))
        assert TOKENS.access_token not in rendered and TOKENS.refresh_token not in rendered


@pytest.mark.parametrize(
    "plaintext",
    [
        b"{}",
        b'{"access_token":"x","refresh_token":"y","extra":1}',
        b'{"access_token":"x","access_token":"x","refresh_token":"y"}',
        b'{"access_token":"","refresh_token":"y"}',
        b'{"access_token":1,"refresh_token":"y"}',
        b'{"access_token":"\\ud800","refresh_token":"y"}',
        b"not-json-synthetic-secret",
        b"\xff",
        b"x" * 16385,
    ],
)
def test_authenticated_malformed_payload_is_rejected(
    plaintext: bytes, security: SessionSecurity, keys: tuple[bytes, bytes]
) -> None:
    nonce = os.urandom(12)
    ciphertext = AESGCM(keys[0]).encrypt(
        nonce, plaintext, _aad("test", "record-1", IDENTITY, "builder-1")
    )
    envelope = EncryptedTokens(key_id="v1", nonce=nonce, ciphertext=ciphertext, version=1)
    with pytest.raises(SessionIntegrityError) as error:
        security.decrypt(**CONTEXT, envelope=envelope)  # type: ignore[arg-type]
    assert error.value.__context__ is None
    assert "not-json-synthetic-secret" not in "".join(traceback.format_exception(error.value))
