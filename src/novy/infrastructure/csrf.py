"""Stateless CSRF tokens for pre-authentication and builder sessions.

The tokens here bind a browser-held secret to a server-held lookup key. They do
not authenticate a user or replace session resolution; callers still validate
the session itself before granting access. All signatures are domain-separated
HMAC-SHA256 values and are safe to compare in constant time.
"""

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass, field

CSRF_COOKIE_NAME = "novy_csrf"
PREAUTH_COOKIE_NAME = "__Host-novy_preauth"
CSRF_HEADER_NAME = "X-CSRF-Token"
PREAUTH_TTL_SECONDS = 900

_PREAUTH_STATE_DOMAIN = b"novy:csrf:preauth:state:v1\0"
_PREAUTH_TOKEN_DOMAIN = b"novy:csrf:preauth:token:v1\0"
_SESSION_TOKEN_DOMAIN = b"novy:csrf:session:token:v1\0"
_ENVIRONMENTS = frozenset({"development", "test", "preview", "production"})
_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class PreauthCsrf:
    state: str
    token: str = field(repr=False)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _decode_canonical_bytes(value: object) -> bytes | None:
    """Decode an unpadded, canonical base64url string without leaking input."""
    if type(value) is not str or not value:
        return None
    try:
        decoded = _b64url_decode(value)
    except (ValueError, binascii.Error):
        return None
    if not decoded or _b64url_encode(decoded) != value:
        return None
    return decoded


def _decode_canonical_int(value: object) -> int | None:
    raw = _decode_canonical_bytes(value)
    if raw is None:
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if not text.isdigit() or (len(text) > 1 and text[0] == "0"):
        return None
    return int(text)


def _decode_credential(value: object) -> bytes | None:
    """Decode the canonical 43-character urlsafe credential for a 32-byte value."""
    if type(value) is not str or _CREDENTIAL.fullmatch(value) is None:
        return None
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(decoded) != 32 or _b64url_encode(decoded) != value:
        return None
    return decoded


def is_canonical_token(value: str) -> bool:
    """Return True only for a non-empty, unpadded, canonical base64url token."""
    return _decode_canonical_bytes(value) is not None


class CsrfService:
    __slots__ = ("_lookup_key", "_environment")

    def __init__(self, *, lookup_key: bytes, environment: str) -> None:
        if (
            type(lookup_key) is not bytes
            or len(lookup_key) != 32
            or type(environment) is not str
            or environment not in _ENVIRONMENTS
        ):
            raise ValueError("Invalid CSRF service configuration")
        self._lookup_key = lookup_key
        self._environment = environment

    def __repr__(self) -> str:
        return "CsrfService(<redacted>)"

    def is_canonical_token(self, value: str) -> bool:
        return is_canonical_token(value)

    def _sign(self, domain: bytes, payload: bytes) -> bytes:
        return hmac.digest(self._lookup_key, domain + payload, hashlib.sha256)

    def issue_preauth(self) -> PreauthCsrf:
        nonce = secrets.token_bytes(32)
        expiry = int(time.time()) + PREAUTH_TTL_SECONDS
        state_signature = self._sign(_PREAUTH_STATE_DOMAIN, nonce + str(expiry).encode("ascii"))
        state = ".".join(
            (
                _b64url_encode(nonce),
                _b64url_encode(str(expiry).encode("ascii")),
                _b64url_encode(state_signature),
            )
        )
        token = _b64url_encode(self._sign(_PREAUTH_TOKEN_DOMAIN, state.encode("ascii")))
        return PreauthCsrf(state=state, token=token)

    def verify_preauth(self, state: str, token: str) -> bool:
        try:
            if type(state) is not str or type(token) is not str:
                return False
            parts = state.split(".")
            if len(parts) != 3:
                return False
            nonce = _decode_canonical_bytes(parts[0])
            expiry = _decode_canonical_int(parts[1])
            signature = _decode_canonical_bytes(parts[2])
            if (
                nonce is None
                or len(nonce) != 32
                or expiry is None
                or signature is None
                or len(signature) != 32
                or not is_canonical_token(token)
            ):
                return False
            expected_state_signature = self._sign(
                _PREAUTH_STATE_DOMAIN, nonce + str(expiry).encode("ascii")
            )
            if not hmac.compare_digest(signature, expected_state_signature):
                return False
            token_bytes = _b64url_decode(token)
            expected_token = self._sign(_PREAUTH_TOKEN_DOMAIN, state.encode("ascii"))
            if not hmac.compare_digest(token_bytes, expected_token):
                return False
            now = int(time.time())
            return now < expiry <= now + PREAUTH_TTL_SECONDS + 60
        except Exception:
            return False

    def token_for_session(self, credential: str) -> str:
        decoded = _decode_credential(credential)
        if decoded is None:
            raise ValueError("Invalid session credential")
        return _b64url_encode(self._sign(_SESSION_TOKEN_DOMAIN, decoded))

    def verify_session(self, credential: str, token: str) -> bool:
        try:
            decoded = _decode_credential(credential)
            if decoded is None or not is_canonical_token(token):
                return False
            expected = self._sign(_SESSION_TOKEN_DOMAIN, decoded)
            return hmac.compare_digest(_b64url_decode(token), expected)
        except Exception:
            return False
