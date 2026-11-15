import asyncio
import socket
import struct
import traceback
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn, cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from bson import BSON
from bson.codec_options import CodecOptions
from bson.errors import InvalidBSON
from pymongo import ReadPreference, ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError, PyMongoError
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
from novy.infrastructure.builder_sessions import (
    DIGEST_INDEX,
    TTL_INDEX,
    MongoBuilderSessionRepository,
    _document,
)
from novy.infrastructure.migrations.builder_sessions_v1 import ensure_builder_session_indexes

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SECRET = "synthetic-secret"
SESSION = BuilderSession(
    record_id="a" * 32,
    digest=b"d" * 32,
    identity=BuilderIdentity("issuer", "subject"),
    builder_id="builder",
    tokens=EncryptedTokens("key", b"n" * 12, b"c" * 16),
    created_at=NOW,
    last_seen_at=NOW,
    idle_expires_at=NOW + timedelta(days=7),
    absolute_expires_at=NOW + timedelta(days=30),
    revoked_at=None,
    revision=0,
)


def reject_network(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Unexpected network access")


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)


def repository() -> tuple[MongoBuilderSessionRepository, MagicMock, MagicMock, MagicMock]:
    collection, write, read = MagicMock(), MagicMock(), MagicMock()
    collection.with_options.return_value = write
    write.with_options.return_value = read
    write.index_information = AsyncMock(
        return_value={
            DIGEST_INDEX: {"key": [("digest", 1)], "unique": True},
            TTL_INDEX: {"key": [("absolute_expires_at", 1)], "expireAfterSeconds": 0},
        }
    )
    write.insert_one = AsyncMock()
    write.find_one_and_update = AsyncMock(return_value=None)
    read.find_one = AsyncMock(return_value=None)
    repo = MongoBuilderSessionRepository(cast(AsyncCollection[dict[str, Any]], collection))
    return repo, collection, write, read


def run_operation(repo: MongoBuilderSessionRepository, operation: str) -> Any:
    if operation == "insert":
        return asyncio.run(repo.insert(SESSION))
    if operation == "find":
        return asyncio.run(repo.find(SESSION.digest))
    if operation == "touch":
        return asyncio.run(repo.touch(SESSION))
    return asyncio.run(repo.revoke(SESSION))


def test_options_and_migration() -> None:
    _, collection, write, _ = repository()
    collection.with_options.assert_called_once_with(
        codec_options=CodecOptions(document_class=StrictDocument, tz_aware=True, tzinfo=UTC),
        write_concern=WriteConcern(w="majority"),
        read_preference=ReadPreference.PRIMARY,
    )
    write.with_options.assert_called_once_with(read_concern=ReadConcern("linearizable"))
    collection.create_index = AsyncMock()
    asyncio.run(ensure_builder_session_indexes(cast(AsyncCollection[dict[str, Any]], collection)))
    assert collection.create_index.await_args_list == [
        call([("digest", 1)], unique=True, name=DIGEST_INDEX),
        call([("absolute_expires_at", 1)], expireAfterSeconds=0, name=TTL_INDEX),
    ]


@pytest.mark.parametrize("operation", ["insert", "find", "touch", "revoke"])
@pytest.mark.parametrize(
    "bad", ["missing", "ttl", "unique", "partial", "hidden", "sparse", "error"]
)
def test_index_fail_closed(operation: str, bad: str) -> None:
    repo, _, write, read = repository()
    indexes = write.index_information.return_value
    if bad == "missing":
        del indexes[DIGEST_INDEX]
    elif bad == "ttl":
        indexes[TTL_INDEX]["expireAfterSeconds"] = True
    elif bad == "unique":
        indexes[DIGEST_INDEX]["unique"] = False
    elif bad == "partial":
        indexes[TTL_INDEX]["partialFilterExpression"] = {}
    elif bad == "hidden":
        indexes[DIGEST_INDEX]["hidden"] = True
    elif bad == "sparse":
        indexes[DIGEST_INDEX]["sparse"] = True
    else:
        write.index_information.side_effect = PyMongoError(SECRET)
    with pytest.raises(BuilderSessionStoreError) as exc:
        run_operation(repo, operation)
    assert exc.value.__context__ is None
    write.insert_one.assert_not_awaited()
    write.find_one_and_update.assert_not_awaited()
    read.find_one.assert_not_awaited()


def test_insert_and_find_roundtrip_utc_and_encrypted_only() -> None:
    repo, _, write, read = repository()
    asyncio.run(repo.insert(SESSION))
    doc = write.insert_one.await_args.args[0]
    assert doc == _document(SESSION)
    assert set(doc["tokens"]) == {"key_id", "nonce", "ciphertext", "version"}
    options = CodecOptions(document_class=StrictDocument, tz_aware=True, tzinfo=UTC)
    read.find_one.return_value = BSON.encode(doc).decode(codec_options=options)
    assert asyncio.run(repo.find(SESSION.digest)) == SESSION
    assert read.find_one.return_value["created_at"].tzinfo is UTC
    read.find_one.assert_awaited_once_with(
        {"kind": "builder", "digest": SESSION.digest},
        max_time_ms=3000,
        collation={"locale": "simple"},
    )
    assert write.index_information.await_count == 2


@pytest.mark.parametrize("operation", ["touch", "revoke"])
def test_compare_and_swap_pipeline(operation: str) -> None:
    repo, _, write, _ = repository()
    updated = (
        replace(
            SESSION,
            last_seen_at=NOW + timedelta(seconds=1),
            idle_expires_at=NOW + timedelta(days=7, seconds=1),
            revision=1,
        )
        if operation == "touch"
        else replace(SESSION, revoked_at=NOW, revision=1)
    )
    write.find_one_and_update.return_value = _document(updated)
    assert run_operation(repo, operation) == updated
    args, kwargs = write.find_one_and_update.await_args
    query, pipeline = args
    expected = _document(SESSION)
    expected["revoked_at"] = {"$type": 10}
    conditions: list[dict[str, Any]] = [{"$eq": [{"$type": "$revoked_at"}, "null"]}]
    if operation == "touch":
        conditions.extend(
            [
                {"$gte": ["$$NOW", "$created_at"]},
                {"$lt": ["$$NOW", "$idle_expires_at"]},
                {"$lt": ["$$NOW", "$absolute_expires_at"]},
            ]
        )
    expected["$expr"] = {"$and": conditions}
    assert query == expected
    assert kwargs == {
        "upsert": False,
        "return_document": ReturnDocument.AFTER,
        "maxTimeMS": 3000,
        "collation": {"locale": "simple"},
    }
    changes = pipeline[0]["$set"]
    assert changes["revision"] == {"$add": ["$revision", 1]}
    assert "tokens" not in changes
    if operation == "touch":
        maximum = {"$max": ["$last_seen_at", "$$NOW"]}
        assert changes == {
            "last_seen_at": maximum,
            "idle_expires_at": {
                "$min": [
                    "$absolute_expires_at",
                    {"$dateAdd": {"startDate": maximum, "unit": "day", "amount": 7}},
                ]
            },
            "revision": {"$add": ["$revision", 1]},
        }
    else:
        assert changes["revoked_at"] == {"$max": ["$created_at", "$$NOW"]}
    write.find_one_and_update.return_value = None
    assert run_operation(repo, operation) is None
    assert run_operation(repo, operation) is None
    assert write.index_information.await_count == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "app"),
        ("digest", b"x" * 32),
        ("digest", bytearray(b"d" * 32)),
        ("revision", True),
        ("created_at", "2026"),
        ("tokens", {}),
        (
            "tokens",
            {
                "key_id": "key",
                "nonce": b"n" * 12,
                "ciphertext": b"c" * 16,
                "version": 1,
                "access_token": SECRET,
            },
        ),
    ],
)
def test_corrupt_documents_fail_closed(field: str, value: Any) -> None:
    repo, _, _, read = repository()
    doc = _document(SESSION)
    doc[field] = value
    read.find_one.return_value = doc
    with pytest.raises(BuilderSessionStoreError) as exc:
        asyncio.run(repo.find(SESSION.digest))
    assert exc.value.__context__ is None


@pytest.mark.parametrize("field", list(_document(SESSION)))
def test_missing_fields_rejected(field: str) -> None:
    repo, _, _, read = repository()
    doc = _document(SESSION)
    del doc[field]
    read.find_one.return_value = doc
    with pytest.raises(BuilderSessionStoreError):
        asyncio.run(repo.find(SESSION.digest))


def test_duplicate_bson_rejected_and_sanitized() -> None:
    repo, _, _, read = repository()
    raw: bytes = BSON.encode(_document(SESSION))
    duplicate = BSON.encode({"revoked_at": None})[4:-1]
    raw = struct.pack("<i", len(raw) + len(duplicate)) + raw[4:-1] + duplicate + b"\0"
    options = CodecOptions(document_class=StrictDocument, tz_aware=True, tzinfo=UTC)
    read.find_one.side_effect = lambda *args, **kwargs: BSON(raw).decode(codec_options=options)
    with pytest.raises(BuilderSessionStoreError) as exc:
        asyncio.run(repo.find(SESSION.digest))
    assert exc.value.__context__ is None


@pytest.mark.parametrize("operation", ["insert", "find", "touch", "revoke"])
@pytest.mark.parametrize(
    "error",
    [PyMongoError(SECRET), DuplicateKeyError(SECRET), InvalidBSON(SECRET), ValueError(SECRET)],
)
def test_errors_are_sanitized(operation: str, error: Exception) -> None:
    repo, _, write, read = repository()
    write.insert_one.side_effect = error
    write.find_one_and_update.side_effect = error
    read.find_one.side_effect = error
    with pytest.raises(BuilderSessionStoreError) as exc:
        run_operation(repo, operation)
    assert exc.value.__context__ is None
    assert SECRET not in "".join(traceback.format_exception(exc.value))


@pytest.mark.parametrize("operation", ["touch", "revoke"])
def test_foreign_return_rejected(operation: str) -> None:
    repo, _, write, _ = repository()
    write.find_one_and_update.return_value = _document(
        replace(
            SESSION,
            builder_id="foreign",
            revision=1,
            revoked_at=NOW if operation == "revoke" else None,
        )
    )
    with pytest.raises(BuilderSessionStoreError):
        run_operation(repo, operation)


def test_invalid_inputs_do_not_query() -> None:
    repo, _, write, read = repository()
    for digest in (b"short", cast(Any, {"$ne": None})):
        with pytest.raises(BuilderSessionInvalid):
            asyncio.run(repo.find(digest))
    broken = replace(SESSION)
    object.__setattr__(broken.tokens, "nonce", b"bad")
    try:
        for operation in (repo.insert, repo.touch, repo.revoke):
            with pytest.raises(BuilderSessionInvalid):
                asyncio.run(operation(broken))
    finally:
        object.__setattr__(broken.tokens, "nonce", b"n" * 12)
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(repo.insert(replace(SESSION, revision=1)))
    with pytest.raises(BuilderSessionInvalid):
        asyncio.run(repo.revoke(replace(SESSION, revoked_at=NOW)))
    write.index_information.assert_not_awaited()
    read.find_one.assert_not_awaited()
