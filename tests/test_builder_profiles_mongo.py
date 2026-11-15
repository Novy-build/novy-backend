import asyncio
import os
import socket
import struct
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from bson import BSON, ObjectId
from bson.codec_options import CodecOptions
from bson.errors import InvalidBSON
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pymongo import ASCENDING, ReadPreference
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from novy.application.builder_profiles import (
    BuilderProfileStoreError,
    require_active_builder_profile,
)
from novy.domain.builder import (
    BuilderAccessDenied,
    BuilderIdentity,
    BuilderProfileAlreadyExists,
    BuilderProfileDataError,
)
from novy.infrastructure.bson_document import StrictDocument as _StrictDocument
from novy.infrastructure.builder_profiles import (
    BUILDER_IDENTITY_INDEX,
    MongoBuilderProfileRepository,
    _profile_id,
)
from novy.infrastructure.migrations.builder_profiles_v1 import ensure_builder_profile_indexes

IDENTITY = BuilderIdentity("https://issuer.example.test/auth/v1", "subject-123")
NOW = datetime(2026, 1, 2, 3, 4, 5, 123000, tzinfo=UTC)
SENSITIVE = "synthetic-password-token-secret"


def reject_network(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Unexpected network access")


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in tuple(os.environ):
        if name.upper().startswith(("NOVY_", "MONGO", "SUPABASE", "NEXT_PUBLIC_SUPABASE_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)


def make_repository() -> tuple[MongoBuilderProfileRepository, MagicMock, MagicMock, MagicMock]:
    collection, write, read = MagicMock(), MagicMock(), MagicMock()
    collection.with_options.return_value = write
    write.with_options.return_value = read
    write.insert_one = AsyncMock()
    write.index_information = AsyncMock(
        return_value={
            BUILDER_IDENTITY_INDEX: {
                "key": [("issuer", ASCENDING), ("subject", ASCENDING)],
                "unique": True,
            }
        }
    )
    read.find_one = AsyncMock()
    repository = MongoBuilderProfileRepository(cast(AsyncCollection[dict[str, Any]], collection))
    return repository, collection, write, read


def document() -> dict[str, Any]:
    return {
        "_id": _profile_id(IDENTITY),
        "issuer": IDENTITY.issuer,
        "subject": IDENTITY.subject,
        "display_name": "Builder Name",
        "email": "builder@example.test",
        "is_active": True,
        "created_at": NOW,
        "updated_at": NOW,
    }


def assert_no_writes(write: MagicMock, read: MagicMock) -> None:
    write.insert_one.assert_not_awaited()
    for collection in (write, read):
        for method in ("update_one", "update_many", "replace_one", "find_one_and_update"):
            getattr(collection, method).assert_not_called()


def test_collection_options() -> None:
    _, collection, write, read = make_repository()
    collection.with_options.assert_called_once_with(
        codec_options=CodecOptions(tz_aware=True, tzinfo=UTC),
        write_concern=WriteConcern(w="majority"),
        read_preference=ReadPreference.PRIMARY,
    )
    write.with_options.assert_called_once_with(
        codec_options=CodecOptions(document_class=_StrictDocument, tz_aware=True, tzinfo=UTC),
        read_preference=ReadPreference.PRIMARY,
        read_concern=ReadConcern("linearizable"),
    )
    assert write is not read


def test_create_inserts_exact_profile() -> None:
    repository, _, write, read = make_repository()
    before = datetime.now(UTC)
    profile = asyncio.run(
        repository.create(IDENTITY, display_name="Builder Name", email="b@x.test")
    )
    after = datetime.now(UTC)
    write.insert_one.assert_awaited_once()
    inserted = write.insert_one.await_args.args[0]
    assert set(inserted) == {
        "_id",
        "issuer",
        "subject",
        "display_name",
        "email",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert isinstance(inserted["_id"], ObjectId)
    assert str(inserted["_id"]) == profile.id
    assert inserted["issuer"] == IDENTITY.issuer
    assert inserted["subject"] == IDENTITY.subject
    assert inserted["display_name"] == profile.display_name == "Builder Name"
    assert inserted["email"] == profile.email == "b@x.test"
    assert inserted["is_active"] is profile.is_active is True
    assert profile.identity == IDENTITY
    assert (
        inserted["created_at"] == inserted["updated_at"] == profile.created_at == profile.updated_at
    )
    assert profile.created_at.tzinfo is UTC
    assert profile.created_at.microsecond % 1000 == 0
    assert (
        before.replace(microsecond=(before.microsecond // 1000) * 1000)
        <= profile.created_at
        <= after
    )
    read.find_one.assert_not_awaited()
    write.update_one.assert_not_called()
    write.replace_one.assert_not_called()


@pytest.mark.parametrize("issuer,subject", [("", "s"), ("i", " "), (None, "s"), ("i", 1)])
def test_invalid_identity_rejected_before_insert(issuer: Any, subject: Any) -> None:
    _, _, write, read = make_repository()
    with pytest.raises(ValueError):
        BuilderIdentity(issuer, subject)
    assert_no_writes(write, read)


@pytest.mark.parametrize(
    "field,value",
    [
        ("display_name", ""),
        ("display_name", "  "),
        ("display_name", None),
        ("email", ""),
        ("email", "  "),
        ("email", 1),
    ],
)
def test_create_rejects_invalid_input(field: str, value: Any) -> None:
    repository, _, write, read = make_repository()
    values = {"display_name": "Name", "email": "a@example.test"}
    values[field] = value
    with pytest.raises(ValueError):
        asyncio.run(repository.create(IDENTITY, **values))
    assert_no_writes(write, read)


def test_create_rejects_non_identity() -> None:
    repository, _, write, read = make_repository()
    with pytest.raises(ValueError):
        asyncio.run(
            repository.create(
                cast(Any, {"issuer": "x", "subject": "y"}),
                display_name="Name",
                email="a@example.test",
            )
        )
    assert_no_writes(write, read)


def assert_safe_exception(error: Exception) -> None:
    assert error.__cause__ is None
    assert error.__suppress_context__
    assert SENSITIVE not in str(error)
    assert SENSITIVE not in "".join(traceback.format_exception(error))
    context = error.__context__
    while context is not None:
        assert SENSITIVE not in str(context)
        context = context.__context__


def test_duplicate_create_is_not_upsert_or_reactivation() -> None:
    repository, _, write, read = make_repository()
    write.insert_one.side_effect = DuplicateKeyError(SENSITIVE, details={"keyPattern": {"_id": 1}})
    read.find_one.return_value = {**document(), "is_active": False}
    with pytest.raises(BuilderProfileAlreadyExists) as exc:
        asyncio.run(repository.create(IDENTITY, display_name="Name", email="a@example.test"))
    assert_safe_exception(exc.value)
    write.insert_one.assert_awaited_once()
    read.find_one.assert_awaited_once()
    for method in ("update_one", "update_many", "replace_one", "find_one_and_update"):
        getattr(write, method).assert_not_called()


def test_id_collision_without_matching_identity_is_storage_error() -> None:
    repository, _, write, read = make_repository()
    write.insert_one.side_effect = DuplicateKeyError(SENSITIVE, details={"keyPattern": {"_id": 1}})
    read.find_one.return_value = None
    with pytest.raises(BuilderProfileStoreError) as exc:
        asyncio.run(repository.create(IDENTITY, display_name="Name", email="a@example.test"))
    assert_safe_exception(exc.value)
    read.find_one.assert_awaited_once()
    write.update_one.assert_not_called()


@pytest.mark.parametrize(
    "pattern,expected",
    [
        ({"issuer": 1, "subject": 1}, BuilderProfileAlreadyExists),
        ({"email": 1}, BuilderProfileStoreError),
        ({"_id": 1, "email": 1}, BuilderProfileStoreError),
        ({"subject": 1, "issuer": 1}, BuilderProfileStoreError),
        ({"_id": True}, BuilderProfileStoreError),
        (None, BuilderProfileStoreError),
    ],
)
def test_duplicate_key_classification(pattern: Any, expected: type[Exception]) -> None:
    repository, _, write, read = make_repository()
    write.insert_one.side_effect = DuplicateKeyError(SENSITIVE, details={"keyPattern": pattern})
    with pytest.raises(expected) as exc:
        asyncio.run(repository.create(IDENTITY, display_name="Name", email="a@example.test"))
    assert_safe_exception(exc.value)
    assert exc.value.__context__ is None
    read.find_one.assert_not_awaited()
    write.update_one.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "find"])
@pytest.mark.parametrize(
    "index",
    [
        None,
        {},
        {"key": [("subject", 1), ("issuer", 1)], "unique": True},
        {"key": [("issuer", 1), ("subject", -1)], "unique": True},
        {"key": [("issuer", 1), ("subject", 1)]},
        {"key": [("issuer", 1), ("subject", 1)], "unique": 1},
        {"key": [("issuer", 1), ("subject", 1)], "unique": True, "sparse": True},
        {"key": [("issuer", 1), ("subject", 1)], "unique": True, "hidden": True},
        {"key": [("issuer", 1), ("subject", 1)], "unique": True, "partialFilterExpression": {}},
        {"key": [("issuer", 1), ("subject", 1)], "unique": True, "collation": None},
        {"key": [("issuer", 1), ("subject", 1)], "unique": True, "collation": {"locale": "en"}},
    ],
)
def test_index_precondition(operation: str, index: Any) -> None:
    repository, _, write, read = make_repository()
    write.index_information.return_value = {} if index is None else {BUILDER_IDENTITY_INDEX: index}
    with pytest.raises(BuilderProfileStoreError) as exc:
        if operation == "create":
            asyncio.run(repository.create(IDENTITY, display_name="Name", email="a@example.test"))
        else:
            asyncio.run(repository.find_by_identity(IDENTITY))
    assert_safe_exception(exc.value)
    assert_no_writes(write, read)
    read.find_one.assert_not_awaited()


@pytest.mark.parametrize("operation", ["create", "find"])
def test_index_list_failure_is_sanitized(operation: str) -> None:
    repository, _, write, read = make_repository()
    write.index_information.side_effect = PyMongoError(SENSITIVE)
    with pytest.raises(BuilderProfileStoreError) as exc:
        if operation == "create":
            asyncio.run(repository.create(IDENTITY, display_name="Name", email="a@example.test"))
        else:
            asyncio.run(repository.find_by_identity(IDENTITY))
    assert_safe_exception(exc.value)
    assert exc.value.__context__ is None
    assert_no_writes(write, read)
    read.find_one.assert_not_awaited()


def test_deterministic_ids_are_identity_and_realm_scoped() -> None:
    repository, _, write, _ = make_repository()
    other = BuilderIdentity("https://other.example.test/auth/v1", IDENTITY.subject)
    first = asyncio.run(repository.create(IDENTITY, display_name="Name", email="a@example.test"))
    second = asyncio.run(repository.create(IDENTITY, display_name="Name", email="a@example.test"))
    third = asyncio.run(repository.create(other, display_name="Name", email="a@example.test"))
    assert first.id == second.id != third.id
    assert write.index_information.await_count == 3


@pytest.mark.parametrize(
    "error", [InvalidBSON(SENSITIVE), ValueError(SENSITIVE), OverflowError(SENSITIVE)]
)
def test_decode_failures_are_sanitized(error: Exception) -> None:
    repository, _, _, read = make_repository()
    read.find_one.side_effect = error
    with pytest.raises(BuilderProfileDataError) as exc:
        asyncio.run(repository.find_by_identity(IDENTITY))
    assert_safe_exception(exc.value)
    assert exc.value.__context__ is None


def test_real_bson_decoder_rejects_duplicate_status() -> None:
    repository, _, _, read = make_repository()
    original = BSON.encode(document())
    duplicate = BSON.encode({"is_active": False})[4:-1]
    raw = struct.pack("<i", len(original) + len(duplicate)) + original[4:-1] + duplicate + b"\x00"
    options = CodecOptions(document_class=_StrictDocument, tz_aware=True, tzinfo=UTC)

    def decode(*args: Any, **kwargs: Any) -> Any:
        return BSON(raw).decode(codec_options=options)

    with pytest.raises(ValueError):
        decode()
    read.find_one.side_effect = decode
    with pytest.raises(BuilderProfileDataError) as exc:
        asyncio.run(repository.find_by_identity(IDENTITY))
    assert_safe_exception(exc.value)
    assert exc.value.__context__ is None


@pytest.mark.parametrize("operation", ["create", "find"])
def test_database_error_is_generic_and_sanitized(operation: str) -> None:
    repository, _, write, read = make_repository()
    write.insert_one.side_effect = PyMongoError(SENSITIVE)
    read.find_one.side_effect = PyMongoError(SENSITIVE)
    with pytest.raises(BuilderProfileStoreError) as exc:
        if operation == "create":
            asyncio.run(repository.create(IDENTITY, display_name="Name", email="a@example.test"))
        else:
            asyncio.run(repository.find_by_identity(IDENTITY))
    assert str(exc.value) == "Builder profile storage is unavailable"
    assert_safe_exception(exc.value)


@pytest.mark.parametrize("active", [True, False])
def test_find_maps_exact_document_and_bounded_query(active: bool) -> None:
    repository, _, write, read = make_repository()
    stored = document()
    stored["is_active"] = active
    read.find_one.return_value = stored
    profile = asyncio.run(repository.find_by_identity(IDENTITY))
    assert profile is not None
    assert profile.id == str(stored["_id"])
    assert profile.identity == IDENTITY
    assert profile.display_name == stored["display_name"]
    assert profile.email == stored["email"]
    assert profile.is_active is active
    assert profile.created_at == profile.updated_at == NOW
    read.find_one.assert_awaited_once_with(
        {"_id": _profile_id(IDENTITY), "issuer": IDENTITY.issuer, "subject": IDENTITY.subject},
        collation={"locale": "simple"},
        max_time_ms=3000,
    )
    assert_no_writes(write, read)


def test_find_missing_and_invalid_identity() -> None:
    repository, _, write, read = make_repository()
    read.find_one.return_value = None
    assert asyncio.run(repository.find_by_identity(IDENTITY)) is None
    with pytest.raises(ValueError):
        asyncio.run(repository.find_by_identity(cast(Any, {"issuer": {"$ne": "x"}})))
    read.find_one.assert_awaited_once()
    assert_no_writes(write, read)


@pytest.mark.parametrize("field", list(document()))
def test_find_rejects_missing_fields(field: str) -> None:
    repository, _, write, read = make_repository()
    stored = document()
    del stored[field]
    read.find_one.return_value = stored
    with pytest.raises(BuilderProfileDataError):
        asyncio.run(repository.find_by_identity(IDENTITY))
    assert_no_writes(write, read)


@pytest.mark.parametrize(
    "field,value",
    [
        ("_id", None),
        ("_id", "0123456789abcdef01234567"),
        ("_id", ObjectId("0123456789abcdef01234567")),
        ("issuer", None),
        ("issuer", "other"),
        ("subject", {"$ne": "x"}),
        ("subject", "other"),
        ("display_name", None),
        ("display_name", " "),
        ("email", 1),
        ("email", ""),
        ("is_active", None),
        ("is_active", 1),
        ("is_active", "true"),
        ("created_at", None),
        ("created_at", datetime(2026, 1, 1)),
        ("updated_at", "2026-01-01"),
        ("updated_at", datetime(2026, 1, 1)),
    ],
)
def test_find_rejects_corruption(field: str, value: Any) -> None:
    repository, _, write, read = make_repository()
    stored = document()
    stored[field] = value
    read.find_one.return_value = stored
    with pytest.raises(BuilderProfileDataError):
        asyncio.run(repository.find_by_identity(IDENTITY))
    assert_no_writes(write, read)


@pytest.mark.parametrize("result", [None, False])
def test_require_active_denies_missing_or_disabled(result: bool | None) -> None:
    repository, _, write, read = make_repository()
    stored = document()
    stored["is_active"] = result
    read.find_one.return_value = None if result is None else stored
    with pytest.raises(BuilderAccessDenied):
        asyncio.run(require_active_builder_profile(repository, IDENTITY))
    assert_no_writes(write, read)


@pytest.mark.parametrize("corrupt", [True, False])
def test_require_active_propagates_data_and_storage_errors(corrupt: bool) -> None:
    repository, _, write, read = make_repository()
    if corrupt:
        read.find_one.return_value = {"is_active": 1}
    else:
        read.find_one.side_effect = PyMongoError(SENSITIVE)
    expected = BuilderProfileDataError if corrupt else BuilderProfileStoreError
    with pytest.raises(expected):
        asyncio.run(require_active_builder_profile(repository, IDENTITY))
    assert_no_writes(write, read)


def test_disable_is_visible_on_next_lookup_without_cache() -> None:
    repository, _, write, read = make_repository()
    stored = document()
    read.find_one.side_effect = lambda *args, **kwargs: stored.copy()
    assert asyncio.run(require_active_builder_profile(repository, IDENTITY)).is_active is True
    stored["is_active"] = False
    with pytest.raises(BuilderAccessDenied):
        asyncio.run(require_active_builder_profile(repository, IDENTITY))
    assert read.find_one.await_count == 2
    assert_no_writes(write, read)


def test_index_migration_is_explicit_and_repeatable() -> None:
    collection = MagicMock()
    collection.create_index = AsyncMock(return_value="builder_identity_unique_v1")
    typed = cast(AsyncCollection[dict[str, Any]], collection)
    asyncio.run(ensure_builder_profile_indexes(typed))
    asyncio.run(ensure_builder_profile_indexes(typed))
    expected = call(
        [("issuer", ASCENDING), ("subject", ASCENDING)],
        unique=True,
        name="builder_identity_unique_v1",
        collation={"locale": "simple"},
    )
    assert collection.create_index.await_args_list == [expected, expected]
    assert collection.mock_calls == [call.create_index(*expected.args, **expected.kwargs)] * 2


def test_startup_does_not_issue_index_ddl(monkeypatch: pytest.MonkeyPatch) -> None:
    import novy.main as main
    from novy.infrastructure.settings import Settings

    client = MagicMock()
    client.close = AsyncMock()
    monkeypatch.setattr(main, "create_mongo_client", MagicMock(return_value=client))
    settings = Settings(mongodb_connection_string=SecretStr("mongodb://synthetic.example.test"))
    with TestClient(main.create_app(settings)):
        client.__getitem__.assert_called_once_with("platform_db")
        assert len(client.mock_calls) == 1
    client.close.assert_awaited_once_with()
    assert len(client.mock_calls) == 2
