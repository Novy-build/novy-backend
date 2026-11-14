"""Opt-in database verification; this is not Compass UI or authenticated request coverage."""

import asyncio
import logging
import socket
from copy import deepcopy
from typing import Any, NoReturn, cast
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import UUID, uuid4

import pytest
from bson import ObjectId
from bson.binary import UUID_SUBTYPE, Binary
from pymongo import ASCENDING, ReadPreference
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern

from novy.application.builder_profiles import require_active_builder_profile
from novy.domain.builder import (
    BuilderAccessDenied,
    BuilderIdentity,
    BuilderProfile,
    BuilderProfileAlreadyExists,
    BuilderProfileDataError,
)
from novy.infrastructure.builder_profiles import (
    BUILDER_IDENTITY_INDEX,
    MongoBuilderProfileRepository,
)


def _validator(issuers: tuple[str, str]) -> dict[str, Any]:
    return {"issuer": {"$in": list(issuers)}}


def _exact_filter(document: dict[str, Any]) -> dict[str, Any]:
    return {"_id": document["_id"], "$expr": {"$eq": ["$$ROOT", {"$literal": document}]}}


async def _metadata_uuid(db: Any, name: str, issuers: tuple[str, str]) -> object | None:
    cursor = await db.list_collections(filter={"name": name})
    entries = await cursor.to_list(length=2)
    if len(entries) != 1 or entries[0].get("name") != name:
        return None
    entry = entries[0]
    if entry.get("options", {}).get("validator") != _validator(issuers):
        return None
    value = entry.get("info", {}).get("uuid")
    if isinstance(value, UUID) or (
        isinstance(value, Binary) and value.subtype == UUID_SUBTYPE and len(value) == 16
    ):
        return value
    return None


class _CaptureCollection:
    def __init__(self, collection: Any, documents: list[dict[str, Any]]) -> None:
        self.collection = collection
        self.documents = documents

    def with_options(self, **options: Any) -> "_CaptureCollection":
        return _CaptureCollection(self.collection.with_options(**options), self.documents)

    async def insert_one(self, document: dict[str, Any], **options: Any) -> Any:
        self.documents.append(deepcopy(document))
        return await self.collection.insert_one(document, **options)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.collection, name)


async def _cleanup(
    db: Any,
    name: str,
    issuers: tuple[str, str],
    owned_uuid: object,
    documents: list[dict[str, Any]],
) -> bool:
    try:
        if await _metadata_uuid(db, name, issuers) != owned_uuid:
            return False
        collection = db[name]
        for document in documents:
            result = await collection.delete_one(
                _exact_filter(document), collation={"locale": "simple"}
            )
            if not result.acknowledged:
                return False
        for document in documents:
            if (
                await collection.find_one({"_id": document["_id"]}, projection={"_id": 1})
                is not None
            ):
                return False
        if await collection.find_one({}, projection={"_id": 1}) is not None:
            return False
        return await _metadata_uuid(db, name, issuers) == owned_uuid
    except Exception:
        return False


async def _verify(db: Any, other_db: Any) -> list[str]:
    name = f"nv_bp_{uuid4().hex}"
    issuers = (f"https://verify.invalid/{uuid4().hex}", f"https://verify.invalid/{uuid4().hex}")
    subject = uuid4().hex
    documents: list[dict[str, Any]] = []
    statuses = [f"scratch collection: {name}"]
    print(statuses[0], flush=True)
    stage = "create"
    owned_uuid: object | None = None
    verified = False
    try:
        collection = await db.create_collection(name, validator=_validator(issuers))
        stage = "metadata"
        owned_uuid = await _metadata_uuid(db, name, issuers)
        if owned_uuid is None:
            raise ValueError

        async def confirm() -> None:
            if await _metadata_uuid(db, name, issuers) != owned_uuid:
                raise ValueError

        stage = "index migration"
        await confirm()
        from novy.infrastructure.migrations.builder_profiles_v1 import (
            ensure_builder_profile_indexes,
        )

        await ensure_builder_profile_indexes(collection)
        indexes = await collection.index_information()
        index = indexes.get(BUILDER_IDENTITY_INDEX, {})
        if (
            index.get("key") != [("issuer", ASCENDING), ("subject", ASCENDING)]
            or index.get("unique") is not True
            or index.get("sparse", False) is not False
            or index.get("hidden", False) is not False
            or "partialFilterExpression" in index
            or ("collation" in index and index["collation"].get("locale") != "simple")
        ):
            raise ValueError
        statuses.append("index migration: confirmed")

        stage = "concurrent create"
        await confirm()
        adapter = _CaptureCollection(collection, documents)
        repo = MongoBuilderProfileRepository(cast(AsyncCollection[dict[str, Any]], adapter))
        identity = BuilderIdentity(issuers[0], subject)
        other_identity = BuilderIdentity(issuers[1], subject)

        async def create(which: BuilderIdentity) -> BuilderProfile:
            return await repo.create(
                which, display_name="Synthetic", email="verify@example.invalid"
            )

        results = await asyncio.gather(create(identity), create(identity), return_exceptions=True)
        profiles = [result for result in results if isinstance(result, BuilderProfile)]
        duplicates = [
            result for result in results if isinstance(result, BuilderProfileAlreadyExists)
        ]
        if len(profiles) != 1 or len(duplicates) != 1:
            raise ValueError

        stage = "compound uniqueness enforcement"
        await confirm()
        if set(indexes) != {"_id_", BUILDER_IDENTITY_INDEX}:
            raise ValueError
        winner = next(
            doc
            for doc in documents
            if str(doc["_id"]) == profiles[0].id and doc["created_at"] == profiles[0].created_at
        )
        duplicate_identity = {**winner, "_id": ObjectId()}
        documents.append(deepcopy(duplicate_identity))
        try:
            await collection.insert_one(duplicate_identity)
        except DuplicateKeyError:
            pass
        else:
            raise ValueError
        statuses.append("compound unique index enforcement: confirmed")

        stage = "other issuer create"
        await confirm()
        other_profile = await create(other_identity)
        if profiles[0].id == other_profile.id:
            raise ValueError
        if await require_active_builder_profile(repo, identity) != profiles[0]:
            raise ValueError
        if await require_active_builder_profile(repo, other_identity) != other_profile:
            raise ValueError
        statuses.append("concurrency, issuer isolation and active lookup: confirmed")

        stage = "majority disable"
        await confirm()
        # Concurrent attempts may differ only in their internally generated timestamps.
        initial = [doc for doc in documents if doc["issuer"] == issuers[0]]
        disabled = [{**doc, "is_active": False} for doc in initial]
        malformed = [{**doc, "is_active": "invalid"} for doc in initial]
        documents.extend(deepcopy(disabled + malformed))
        external = other_db[name].with_options(write_concern=WriteConcern(w="majority"))
        changed = 0
        for doc in initial:
            result = await external.update_one(
                _exact_filter(doc), {"$set": {"is_active": False}}, collation={"locale": "simple"}
            )
            if not result.acknowledged:
                raise ValueError
            changed += result.modified_count
        if changed != 1:
            raise ValueError
        try:
            await require_active_builder_profile(repo, identity)
        except BuilderAccessDenied:
            pass
        else:
            raise ValueError

        stage = "duplicate after disable"
        await confirm()
        try:
            await create(identity)
        except BuilderProfileAlreadyExists:
            pass
        else:
            raise ValueError
        try:
            await require_active_builder_profile(repo, identity)
        except BuilderAccessDenied:
            pass
        else:
            raise ValueError

        stage = "malformed status"
        await confirm()
        changed = 0
        for doc in disabled:
            result = await external.update_one(
                _exact_filter(doc),
                {"$set": {"is_active": "invalid"}},
                collation={"locale": "simple"},
            )
            if not result.acknowledged:
                raise ValueError
            changed += result.modified_count
        if changed != 1:
            raise ValueError
        try:
            await require_active_builder_profile(repo, identity)
        except BuilderProfileDataError:
            pass
        else:
            raise ValueError
        statuses.append("majority write and repository lookup: confirmed (not Compass UI or HTTP)")

        verified = True
    except Exception:
        statuses.append(f"{stage}: failed or inconclusive; investigate {name}")
    finally:
        if owned_uuid is not None and documents:
            cleaned = await _cleanup(db, name, issuers, owned_uuid, documents)
            if verified and cleaned:
                statuses.append(
                    "synthetic records absent; collection and index intentionally retained"
                )
            elif not verified:
                statuses.append(f"cleanup attempted after failure; investigate {name}")
            else:
                statuses.append(f"cleanup failed or inconclusive; investigate {name}")
    return statuses


async def _run_live() -> list[str]:
    statuses: list[str] = []
    stage = "configuration or app startup"
    try:
        from novy.infrastructure.mongodb import create_mongo_client
        from novy.infrastructure.settings import Settings
        from novy.main import create_app

        settings = Settings()
        if settings.mongodb_connection_string is None:
            return ["configuration: unavailable"]
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            stage = "database selection"
            db = getattr(app.state, "platform_database", None)
            client = getattr(app.state, "mongo_client", None)
            if (
                client is None
                or db is None
                or db.name != "platform_db"
                or not db.write_concern.acknowledged
            ):
                return ["database selection: unavailable"]
            db = db.with_options(
                read_preference=ReadPreference.PRIMARY, write_concern=WriteConcern(w="majority")
            )
            other_client = create_mongo_client(settings.mongodb_connection_string)
            try:
                other_db = other_client["platform_db"].with_options(
                    read_preference=ReadPreference.PRIMARY
                )
                stage = "verification"
                statuses = await _verify(db, other_db)
            finally:
                try:
                    await other_client.close()
                except Exception:
                    statuses.append("second client shutdown: failed or inconclusive")
            stage = "app shutdown"
    except Exception:
        statuses.append(f"{stage}: failed or inconclusive")
    return statuses


def _run_if_authorized(enabled: bool) -> list[str] | None:
    if not enabled:
        return None
    level = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        return asyncio.run(_run_live())
    except Exception:
        return ["verification execution: failed or inconclusive"]
    finally:
        logging.disable(level)


def test_builder_profiles_live(request: pytest.FixtureRequest) -> None:
    statuses = _run_if_authorized(request.config.getoption("--verify-builder-profiles-db"))
    if statuses is None:
        pytest.skip("pass --verify-builder-profiles-db to authorize scratch writes")
    for status in statuses:
        if status.startswith("scratch collection: "):
            continue
        print(status)
    if (
        not statuses
        or statuses[-1] != "synthetic records absent; collection and index intentionally retained"
    ):
        pytest.fail(
            "builder profile verification failed or inconclusive; see safe stages", pytrace=False
        )


def _fake_db(name: str, issuers: tuple[str, str], owned_uuid: UUID) -> MagicMock:
    db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(
        return_value=[
            {
                "name": name,
                "info": {"uuid": owned_uuid},
                "options": {"validator": _validator(issuers)},
            }
        ]
    )
    db.list_collections = AsyncMock(return_value=cursor)
    db.create_collection = AsyncMock(return_value=db[name])
    db[name].delete_one = AsyncMock(return_value=MagicMock(acknowledged=True))
    db[name].find_one = AsyncMock(return_value=None)
    return db


def test_gate_never_calls_live() -> None:
    with patch(__name__ + "._run_live") as live:
        assert _run_if_authorized(False) is None
    live.assert_not_called()


def test_capture_shared_across_options_before_failed_insert() -> None:
    collection = MagicMock()
    collection.with_options.return_value = collection
    collection.insert_one = AsyncMock(side_effect=RuntimeError("secret"))
    documents: list[dict[str, Any]] = []
    adapter = _CaptureCollection(collection, documents).with_options()
    original: dict[str, Any] = {"_id": "synthetic", "nested": {"value": 1}}
    with pytest.raises(RuntimeError):
        asyncio.run(adapter.insert_one(original))
    original["nested"]["value"] = 2
    assert documents == [{"_id": "synthetic", "nested": {"value": 1}}]


def test_exact_cleanup_and_retention() -> None:
    name, issuers, owned_uuid = "nv_bp_safe", ("one", "two"), uuid4()
    db = _fake_db(name, issuers, owned_uuid)
    docs = [{"_id": "id", "is_active": True}, {"_id": "id", "is_active": False}]
    assert asyncio.run(_cleanup(db, name, issuers, owned_uuid, docs))
    assert db[name].delete_one.await_args_list == [
        call(
            {"_id": "id", "$expr": {"$eq": ["$$ROOT", {"$literal": doc}]}},
            collation={"locale": "simple"},
        )
        for doc in docs
    ]
    db[name].delete_many.assert_not_called()
    db.drop_collection.assert_not_called()
    db.drop_database.assert_not_called()


@pytest.mark.parametrize("mismatch", ["missing", "uuid", "validator"])
def test_cleanup_refuses_unconfirmed_metadata(mismatch: str) -> None:
    name, issuers, owned_uuid = "nv_bp_safe", ("one", "two"), uuid4()
    db = _fake_db(name, issuers, owned_uuid)
    if mismatch == "missing":
        db.list_collections.return_value.to_list.return_value = []
    elif mismatch == "uuid":
        db = _fake_db(name, issuers, uuid4())
    else:
        db = _fake_db(name, ("other", "two"), owned_uuid)
    assert not asyncio.run(_cleanup(db, name, issuers, owned_uuid, [{"_id": "id"}]))
    db[name].delete_one.assert_not_awaited()


def test_cleanup_changed_metadata_or_unknown_document_is_inconclusive() -> None:
    name, issuers, owned_uuid = "nv_bp_safe", ("one", "two"), uuid4()
    db = _fake_db(name, issuers, owned_uuid)
    original = db.list_collections.return_value.to_list.return_value
    db.list_collections.return_value.to_list.side_effect = [original, []]
    assert not asyncio.run(_cleanup(db, name, issuers, owned_uuid, [{"_id": "id"}]))
    db = _fake_db(name, issuers, owned_uuid)
    db[name].find_one.return_value = {"_id": "unknown"}
    assert not asyncio.run(_cleanup(db, name, issuers, owned_uuid, [{"_id": "id"}]))


def test_unknown_create_is_redacted_and_never_cleans(capsys: pytest.CaptureFixture[str]) -> None:
    db = MagicMock()
    db.create_collection = AsyncMock(side_effect=RuntimeError("secret URI hostname"))
    statuses = asyncio.run(_verify(db, MagicMock()))
    assert "failed or inconclusive" in statuses[-1]
    assert "secret" not in str(statuses) + capsys.readouterr().out
    db.list_collections.assert_not_called()
    db.__getitem__.assert_not_called()


def test_missing_metadata_after_create_never_mutates_or_cleans() -> None:
    name = "nv_bp_" + UUID(int=0).hex
    db = _fake_db(name, ("one", "two"), uuid4())
    db.list_collections.return_value.to_list.return_value = []
    with patch(__name__ + ".uuid4", side_effect=[UUID(int=i) for i in range(4)]):
        statuses = asyncio.run(_verify(db, MagicMock()))
    assert statuses[-1] == f"metadata: failed or inconclusive; investigate {name}"
    db[name].create_index.assert_not_called()
    db[name].delete_one.assert_not_awaited()


def test_unknown_cleanup_is_redacted_and_not_retried() -> None:
    name, issuers, owned_uuid = "nv_bp_safe", ("one", "two"), uuid4()
    db = _fake_db(name, issuers, owned_uuid)
    db[name].delete_one.side_effect = RuntimeError("secret")
    assert not asyncio.run(_cleanup(db, name, issuers, owned_uuid, [{"_id": "id"}]))
    assert db[name].delete_one.await_count == 1


@pytest.fixture(autouse=True)
def _offline_network_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.config.getoption("--verify-builder-profiles-db") and request.node.name == (
        "test_builder_profiles_live"
    ):
        return

    def reject(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("network disabled")

    monkeypatch.setattr(socket, "getaddrinfo", reject)
    monkeypatch.setattr(socket, "create_connection", reject)
