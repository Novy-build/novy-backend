"""Cryptographic primitives for builder sessions, independent of storage.

Both keys are fixed at v1: replacing either invalidates existing sessions and
requires reauthentication. Nonce volume limits and rotation policy are deferred.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from novy.domain.builder import BuilderIdentity
from novy.domain.builder_sessions import (
    EncryptedTokens,
    ProviderTokens,
    SessionCredentialInvalid,
    SessionIntegrityError,
)

_LOOKUP_DOMAIN = b"novy:builder-session:lookup:v1\0"
_KEY_ID = "v1"
_MAX_JSON = 16384
_MAX_CONTEXT_VALUE = 4096
_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)


def _text(value: object, limit: int) -> bool:
    if type(value) is not str or not value.strip() or len(value) > limit:
        return False
    try:
        return len(value.encode("utf-8")) <= limit
    except UnicodeEncodeError:
        return False


def _json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _aad(environment: str, record_id: str, identity: BuilderIdentity, builder_id: str) -> bytes:
    if not isinstance(identity, BuilderIdentity) or not all(
        _text(value, _MAX_CONTEXT_VALUE)
        for value in (record_id, identity.issuer, identity.subject, builder_id)
    ):
        raise ValueError("Invalid session context")
    return _json(
        [
            "novy:builder-session:tokens:v1",
            environment,
            record_id,
            identity.issuer,
            identity.subject,
            builder_id,
            _KEY_ID,
        ]
    )


class SessionSecurity:
    __slots__ = ("_encryption_key", "_lookup_key", "_environment")

    def __init__(self, *, encryption_key: bytes, lookup_key: bytes, environment: str) -> None:
        if (
            type(encryption_key) is not bytes
            or type(lookup_key) is not bytes
            or len(encryption_key) != 32
            or len(lookup_key) != 32
            or hmac.compare_digest(encryption_key, lookup_key)
            or type(environment) is not str
            or environment not in ("development", "test", "production")
        ):
            raise ValueError("Invalid session security configuration")
        self._encryption_key = encryption_key
        self._lookup_key = lookup_key
        self._environment = environment

    def __repr__(self) -> str:
        return "SessionSecurity(<redacted>)"

    def generate_credential(self) -> str:
        return secrets.token_urlsafe(32)

    def digest(self, credential: str) -> bytes:
        decoded = None
        if type(credential) is str and _CREDENTIAL.fullmatch(credential):
            try:
                candidate = base64.b64decode(credential + "=", altchars=b"-_", validate=True)
                if (
                    len(candidate) == 32
                    and base64.urlsafe_b64encode(candidate).rstrip(b"=").decode("ascii")
                    == credential
                ):
                    decoded = candidate
            except (ValueError, binascii.Error):
                pass
        if decoded is None:
            raise SessionCredentialInvalid()
        return hmac.digest(self._lookup_key, _LOOKUP_DOMAIN + decoded, hashlib.sha256)

    def encrypt(
        self, *, record_id: str, identity: BuilderIdentity, builder_id: str, tokens: ProviderTokens
    ) -> EncryptedTokens:
        result = None
        try:
            if not isinstance(tokens, ProviderTokens) or not all(
                _text(value, _MAX_JSON) for value in (tokens.access_token, tokens.refresh_token)
            ):
                raise ValueError("Invalid provider tokens")
            plaintext = _json(
                {"access_token": tokens.access_token, "refresh_token": tokens.refresh_token}
            )
            if len(plaintext) > _MAX_JSON:
                raise ValueError("Invalid provider tokens")
            aad = _aad(self._environment, record_id, identity, builder_id)
            nonce = os.urandom(12)
            ciphertext = AESGCM(self._encryption_key).encrypt(nonce, plaintext, aad)
            result = EncryptedTokens(key_id=_KEY_ID, nonce=nonce, ciphertext=ciphertext, version=1)
        except (ValueError, TypeError, OverflowError):
            pass
        if result is None:
            raise SessionIntegrityError()
        return result

    def decrypt(
        self,
        *,
        record_id: str,
        identity: BuilderIdentity,
        builder_id: str,
        envelope: EncryptedTokens,
    ) -> ProviderTokens:
        result = None
        try:
            if (
                not isinstance(envelope, EncryptedTokens)
                or type(envelope.key_id) is not str
                or envelope.key_id != _KEY_ID
                or type(envelope.version) is not int
                or envelope.version != 1
                or type(envelope.nonce) is not bytes
                or len(envelope.nonce) != 12
                or type(envelope.ciphertext) is not bytes
                or not 16 <= len(envelope.ciphertext) <= _MAX_JSON + 16
            ):
                raise ValueError("Invalid envelope")
            aad = _aad(self._environment, record_id, identity, builder_id)
            plaintext = AESGCM(self._encryption_key).decrypt(
                envelope.nonce, envelope.ciphertext, aad
            )
            data = json.loads(plaintext.decode("utf-8"))
            if (
                type(data) is not dict
                or set(data) != {"access_token", "refresh_token"}
                or not _text(data["access_token"], _MAX_JSON)
                or not _text(data["refresh_token"], _MAX_JSON)
                or _json(data) != plaintext
            ):
                raise ValueError("Invalid provider tokens")
            result = ProviderTokens(
                access_token=data["access_token"], refresh_token=data["refresh_token"]
            )
        except (InvalidTag, ValueError, TypeError, OverflowError, RecursionError):
            pass
        if result is None:
            raise SessionIntegrityError()
        return result
