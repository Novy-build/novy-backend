"""MongoDB persistence for encrypted builder session snapshots."""

from dataclasses import replace
from datetime import UTC
from typing import Any

from bson.codec_options import CodecOptions
from bson.errors import BSONError
from pymongo import ASCENDING, ReadPreference, ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from novy.domain.builder import BuilderIdentity
from novy.domain.builder_sessions import (
    BuilderSession,
    BuilderSessionInvalid,
    BuilderSessionStoreError,
    EncryptedTokens,
)
from novy.infrastructure.bson_document import StrictDocument

BUILDER_SESSIONS_COLLECTION = "builder_sessions"
DIGEST_INDEX = "builder_session_digest_unique_v1"
TTL_INDEX = "builder_session_absolute_ttl_v1"
_FIELDS = {
    "_id",
    "kind",
    "digest",
    "issuer",
    "subject",
    "builder_id",
    "tokens",
    "created_at",
    "last_seen_at",
    "idle_expires_at",
    "absolute_expires_at",
    "revoked_at",
    "revision",
    "schema_version",
}
_ERRORS = (
    PyMongoError,
    BSONError,
    ValueError,
    TypeError,
    OverflowError,
    UnicodeError,
    KeyError,
    AttributeError,
)


def _document(session: BuilderSession) -> dict[str, Any]:
    if (
        not isinstance(session, BuilderSession)
        or not isinstance(session.identity, BuilderIdentity)
        or not isinstance(session.tokens, EncryptedTokens)
    ):
        raise ValueError
    identity = BuilderIdentity(session.identity.issuer, session.identity.subject)
    tokens = EncryptedTokens(
        session.tokens.key_id,
        session.tokens.nonce,
        session.tokens.ciphertext,
        session.tokens.version,
    )
    checked = replace(session, identity=identity, tokens=tokens)
    return {
        "_id": checked.record_id,
        "kind": "builder",
        "digest": checked.digest,
        "issuer": identity.issuer,
        "subject": identity.subject,
        "builder_id": checked.builder_id,
        "tokens": {
            "key_id": tokens.key_id,
            "nonce": tokens.nonce,
            "ciphertext": tokens.ciphertext,
            "version": tokens.version,
        },
        "created_at": checked.created_at,
        "last_seen_at": checked.last_seen_at,
        "idle_expires_at": checked.idle_expires_at,
        "absolute_expires_at": checked.absolute_expires_at,
        "revoked_at": checked.revoked_at,
        "revision": checked.revision,
        "schema_version": checked.schema_version,
    }


def _decode(document: dict[str, Any]) -> BuilderSession:
    if not isinstance(document, dict) or set(document) != _FIELDS or document["kind"] != "builder":
        raise ValueError
    tokens = document["tokens"]
    if not isinstance(tokens, dict) or set(tokens) != {"key_id", "nonce", "ciphertext", "version"}:
        raise ValueError
    session = BuilderSession(
        record_id=document["_id"],
        digest=document["digest"],
        identity=BuilderIdentity(document["issuer"], document["subject"]),
        builder_id=document["builder_id"],
        tokens=EncryptedTokens(
            tokens["key_id"], tokens["nonce"], tokens["ciphertext"], tokens["version"]
        ),
        created_at=document["created_at"],
        last_seen_at=document["last_seen_at"],
        idle_expires_at=document["idle_expires_at"],
        absolute_expires_at=document["absolute_expires_at"],
        revoked_at=document["revoked_at"],
        revision=document["revision"],
        schema_version=document["schema_version"],
    )
    return session


class MongoBuilderSessionRepository:
    def __init__(self, collection: AsyncCollection[dict[str, Any]]) -> None:
        self._write = collection.with_options(
            codec_options=CodecOptions(document_class=StrictDocument, tz_aware=True, tzinfo=UTC),
            write_concern=WriteConcern(w="majority"),
            read_preference=ReadPreference.PRIMARY,
        )
        self._read = self._write.with_options(read_concern=ReadConcern("linearizable"))

    async def _require_indexes(self) -> None:
        valid = False
        try:
            indexes = await self._write.index_information()
            digest, ttl = indexes.get(DIGEST_INDEX), indexes.get(TTL_INDEX)
            if isinstance(digest, dict) and isinstance(ttl, dict):
                valid = (
                    digest.get("key") == [("digest", ASCENDING)]
                    and type(digest["key"][0][1]) is int
                    and digest.get("unique") is True
                    and ttl.get("key") == [("absolute_expires_at", ASCENDING)]
                    and type(ttl["key"][0][1]) is int
                    and ttl.get("unique", False) is False
                    and type(ttl.get("expireAfterSeconds")) is int
                    and ttl["expireAfterSeconds"] == 0
                    and "expireAfterSeconds" not in digest
                    and all(
                        index.get("sparse", False) is False
                        and index.get("hidden", False) is False
                        and "partialFilterExpression" not in index
                        for index in (digest, ttl)
                    )
                )
        except Exception:
            pass
        if not valid:
            raise BuilderSessionStoreError() from None

    async def insert(self, session: BuilderSession) -> None:
        invalid = False
        try:
            document = _document(session)
            if document["revoked_at"] is not None or document["revision"] != 0:
                raise ValueError
        except _ERRORS:
            invalid = True
        if invalid:
            raise BuilderSessionInvalid() from None
        await self._require_indexes()
        failed = False
        try:
            await self._write.insert_one(StrictDocument(document))
        except _ERRORS:
            failed = True
        if failed:
            raise BuilderSessionStoreError() from None

    async def find(self, digest: bytes) -> BuilderSession | None:
        if type(digest) is not bytes or len(digest) != 32:
            raise BuilderSessionInvalid() from None
        await self._require_indexes()
        try:
            document = await self._read.find_one(
                {"kind": "builder", "digest": digest},
                max_time_ms=3000,
                collation={"locale": "simple"},
            )
            if document is None:
                return None
            session = _decode(document)
            if session.digest != digest:
                raise ValueError
            return session
        except _ERRORS:
            pass
        raise BuilderSessionStoreError() from None

    async def touch(self, expected: BuilderSession) -> BuilderSession | None:
        return await self._mutate(expected, touch=True)

    async def revoke(self, expected: BuilderSession) -> BuilderSession | None:
        return await self._mutate(expected, touch=False)

    async def replace_tokens(
        self, expected: BuilderSession, encrypted: EncryptedTokens
    ) -> BuilderSession | None:
        invalid = False
        encrypted_document: dict[str, Any] = {}
        try:
            query = _document(expected)
            if query["revoked_at"] is not None:
                raise ValueError
            encrypted_document = _document(replace(expected, tokens=encrypted))["tokens"]
            if set(encrypted_document) != {"key_id", "nonce", "ciphertext", "version"}:
                raise ValueError
        except _ERRORS:
            invalid = True
        if invalid:
            raise BuilderSessionInvalid() from None
        query["revoked_at"] = {"$type": 10}
        query["$expr"] = {"$eq": [{"$type": "$revoked_at"}, "null"]}
        await self._require_indexes()
        try:
            document = await self._write.find_one_and_update(
                query,
                [
                    {
                        "$set": {
                            "tokens": encrypted_document,
                            "revision": {"$add": ["$revision", 1]},
                        }
                    }
                ],
                upsert=False,
                return_document=ReturnDocument.AFTER,
                maxTimeMS=3000,
                collation={"locale": "simple"},
            )
            if document is None:
                return None
            result = _decode(document)
            original = _document(expected)
            actual = _document(result)
            for field in ("tokens", "revision"):
                original.pop(field)
                actual.pop(field)
            if (
                original != actual
                or result.revision != expected.revision + 1
                or result.tokens != encrypted
                or result.revoked_at is not None
                or result.last_seen_at != expected.last_seen_at
                or result.idle_expires_at != expected.idle_expires_at
            ):
                raise ValueError
            return result
        except _ERRORS:
            pass
        raise BuilderSessionStoreError() from None

    async def _mutate(self, expected: BuilderSession, *, touch: bool) -> BuilderSession | None:
        invalid = False
        try:
            query = _document(expected)
            if query["revoked_at"] is not None:
                raise ValueError
        except _ERRORS:
            invalid = True
        if invalid:
            raise BuilderSessionInvalid() from None
        query["revoked_at"] = {"$type": 10}
        conditions: list[dict[str, Any]] = [{"$eq": [{"$type": "$revoked_at"}, "null"]}]
        if touch:
            conditions.extend(
                [
                    {"$gte": ["$$NOW", "$created_at"]},
                    {"$lt": ["$$NOW", "$idle_expires_at"]},
                    {"$lt": ["$$NOW", "$absolute_expires_at"]},
                ]
            )
            last_seen = {"$max": ["$last_seen_at", "$$NOW"]}
            changes: dict[str, Any] = {
                "last_seen_at": last_seen,
                "idle_expires_at": {
                    "$min": [
                        "$absolute_expires_at",
                        {"$dateAdd": {"startDate": last_seen, "unit": "day", "amount": 7}},
                    ]
                },
                "revision": {"$add": ["$revision", 1]},
            }
        else:
            changes = {
                "revoked_at": {"$max": ["$created_at", "$$NOW"]},
                "revision": {"$add": ["$revision", 1]},
            }
        query["$expr"] = {"$and": conditions}
        await self._require_indexes()
        try:
            document = await self._write.find_one_and_update(
                query,
                [{"$set": changes}],
                upsert=False,
                return_document=ReturnDocument.AFTER,
                maxTimeMS=3000,
                collation={"locale": "simple"},
            )
            if document is None:
                return None
            result = _decode(document)
            original = _document(expected)
            actual = _document(result)
            for field in ("revision", "last_seen_at", "idle_expires_at", "revoked_at"):
                original.pop(field)
                actual.pop(field)
            if (
                original != actual
                or result.revision != expected.revision + 1
                or (
                    touch
                    and (
                        result.revoked_at is not None or result.last_seen_at < expected.last_seen_at
                    )
                )
                or (
                    not touch
                    and (
                        result.revoked_at is None
                        or result.last_seen_at != expected.last_seen_at
                        or result.idle_expires_at != expected.idle_expires_at
                    )
                )
            ):
                raise ValueError
            return result
        except _ERRORS:
            pass
        raise BuilderSessionStoreError() from None
