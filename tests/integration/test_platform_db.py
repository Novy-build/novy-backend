import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import UUID, uuid4

import pytest
from bson.binary import UUID_SUBTYPE, Binary
from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError

ROLLBACK_STATUS = "rollback observed and IDs reusable; abort acknowledgement not established"


def _validator(marker: str) -> dict[str, Any]:
    return {"run_marker": {"$eq": marker}}


async def _metadata_uuid(db: Any, name: str, marker: str) -> object | None:
    cursor = await db.list_collections(filter={"name": name})
    entries = await cursor.to_list(length=2)
    if len(entries) != 1 or entries[0].get("name") != name:
        return None
    entry = entries[0]
    if entry.get("options", {}).get("validator") != _validator(marker):
        return None
    value = entry.get("info", {}).get("uuid")
    if isinstance(value, UUID) or (
        isinstance(value, Binary) and value.subtype == UUID_SUBTYPE and len(value) == 16
    ):
        return value
    return None


def _exact_filter(document: dict[str, Any]) -> dict[str, Any]:
    return {"_id": document["_id"], "$expr": {"$eq": ["$$ROOT", {"$literal": document}]}}


async def _cleanup(
    db: Any, name: str, marker: str, owned_uuid: object, documents: list[dict[str, Any]]
) -> str:
    inconclusive = f"cleanup inconclusive; investigate {name}"
    try:
        if await _metadata_uuid(db, name, marker) != owned_uuid:
            return inconclusive
        collection = db[name]
        for document in documents:
            await collection.delete_one(_exact_filter(document), collation={"locale": "simple"})
        for document in documents:
            if (
                await collection.find_one(
                    {"_id": document["_id"]},
                    projection={"_id": 1},
                    collation={"locale": "simple"},
                )
                is not None
            ):
                return inconclusive
        if await collection.find_one({}, projection={"_id": 1}) is not None:
            return inconclusive
        if await _metadata_uuid(db, name, marker) != owned_uuid:
            return inconclusive
    except Exception:
        return inconclusive
    return "cleanup: synthetic records absent"


async def _verify(client: Any, db: Any) -> list[str]:
    name = f"novy_verify_{uuid4().hex}"
    marker = uuid4().hex
    crud = {"_id": uuid4().hex, "run_marker": marker, "synthetic_key": "crud", "value": "initial"}
    updated = {**crud, "value": "updated"}
    duplicate = {"_id": uuid4().hex, "run_marker": marker, "synthetic_key": "crud"}
    commit = [
        {"_id": uuid4().hex, "run_marker": marker, "synthetic_key": f"commit-{i}"} for i in range(2)
    ]
    abort = [
        {"_id": uuid4().hex, "run_marker": marker, "synthetic_key": f"abort-{i}"} for i in range(2)
    ]
    documents = [crud, updated, duplicate, *commit, *abort]
    statuses = [f"scratch collection: {name}"]
    owned_uuid: object | None = None
    created = False
    failed = False
    stage = "create"
    try:
        collection = await db.create_collection(name, validator=_validator(marker))
        created = True
        statuses.append("create: acknowledged")
        stage = "metadata"
        owned_uuid = await _metadata_uuid(db, name, marker)
        if owned_uuid is None:
            raise ValueError
        statuses.append("metadata UUID and validator: confirmed")

        stage = "unique index"
        if await _metadata_uuid(db, name, marker) != owned_uuid:
            raise ValueError
        await collection.create_index("synthetic_key", unique=True)
        statuses.append("unique index: created")

        stage = "read/write and uniqueness"
        if await _metadata_uuid(db, name, marker) != owned_uuid:
            raise ValueError
        await collection.insert_one(crud)
        if await collection.find_one({"_id": crud["_id"]}) != crud:
            raise ValueError
        result = await collection.update_one(
            _exact_filter(crud), {"$set": {"value": "updated"}}, collation={"locale": "simple"}
        )
        if result.modified_count != 1 or await collection.find_one({"_id": crud["_id"]}) != updated:
            raise ValueError
        try:
            await collection.insert_one(duplicate)
        except DuplicateKeyError:
            pass
        else:
            raise ValueError
        result = await collection.delete_one(_exact_filter(updated), collation={"locale": "simple"})
        if result.deleted_count != 1 or await collection.find_one({"_id": crud["_id"]}) is not None:
            raise ValueError
        statuses.append("read/write and uniqueness: confirmed")

        stage = "transaction commit"
        if await _metadata_uuid(db, name, marker) != owned_uuid:
            raise ValueError
        async with client.start_session() as session:
            async with await session.start_transaction(read_preference=ReadPreference.PRIMARY):
                for document in commit:
                    await collection.insert_one(document, session=session)
        for document in commit:
            if await collection.find_one({"_id": document["_id"]}) != document:
                raise ValueError
        statuses.append("transaction commit: confirmed on primary")

        stage = "transaction rollback observation"
        if await _metadata_uuid(db, name, marker) != owned_uuid:
            raise ValueError

        class AbortTransaction(Exception):
            pass

        try:
            async with client.start_session() as session:
                async with await session.start_transaction(read_preference=ReadPreference.PRIMARY):
                    for document in abort:
                        await collection.insert_one(document, session=session)
                    raise AbortTransaction
        except AbortTransaction:
            pass
        for document in abort:
            if await collection.find_one({"_id": document["_id"]}) is not None:
                raise ValueError
        if await _metadata_uuid(db, name, marker) != owned_uuid:
            raise ValueError
        for document in abort:
            await collection.insert_one(document)
            result = await collection.delete_one(
                _exact_filter(document), collation={"locale": "simple"}
            )
            if result.deleted_count != 1:
                raise ValueError
        statuses.append(ROLLBACK_STATUS)
    except Exception:
        failed = True
        statuses.append(f"{stage}: failed or inconclusive")
        if not created:
            statuses.append(f"create outcome unknown; investigate {name}")
        elif owned_uuid is None:
            statuses.append(f"ownership unconfirmed; investigate {name}")
    finally:
        if owned_uuid is not None:
            cleanup = await _cleanup(db, name, marker, owned_uuid, documents)
            if failed and cleanup == "cleanup: synthetic records absent":
                cleanup = f"cleanup inconclusive after failed phase; investigate {name}"
            statuses.append(cleanup)
        if not failed and statuses[-1] == "cleanup: synthetic records absent":
            statuses.append("collection and index intentionally retained")
    return statuses


async def _run_live() -> list[str]:
    stage = "configuration or app startup"
    statuses: list[str] = []
    try:
        from novy.main import create_app

        app = create_app()
        async with app.router.lifespan_context(app):
            stage = "database selection"
            client = getattr(app.state, "mongo_client", None)
            db = getattr(app.state, "platform_database", None)
            if (
                client is None
                or db is None
                or db.name != "platform_db"
                or not db.write_concern.acknowledged
            ):
                return ["database selection: failed or unavailable"]
            db = db.with_options(read_preference=ReadPreference.PRIMARY)
            stage = "verification"
            statuses = await _verify(client, db)
            stage = "app shutdown"
    except Exception:
        statuses.append(f"{stage}: failed or inconclusive")
    return statuses


def _run_if_authorized(enabled: bool) -> list[str] | None:
    if not enabled:
        return None
    previous_logging_level = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        return asyncio.run(_run_live())
    except Exception:
        return ["verification execution: failed or inconclusive"]
    finally:
        logging.disable(previous_logging_level)


def test_platform_db_live(request: pytest.FixtureRequest) -> None:
    statuses = _run_if_authorized(request.config.getoption("--verify-platform-db"))
    if statuses is None:
        pytest.skip("pass --verify-platform-db to authorize scratch writes")
    for status in statuses:
        print(status)
    if (
        statuses[-1] != "collection and index intentionally retained"
        or ROLLBACK_STATUS not in statuses
    ):
        pytest.fail(
            "platform_db verification failed or inconclusive; see safe stages above", pytrace=False
        )


def _fake_db(name: str, marker: str, owned_uuid: UUID) -> MagicMock:
    db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(
        return_value=[
            {
                "name": name,
                "info": {"uuid": owned_uuid},
                "options": {"validator": _validator(marker)},
            }
        ]
    )
    db.list_collections = AsyncMock(return_value=cursor)
    db.create_collection = AsyncMock(return_value=db[name])
    db[name].delete_one = AsyncMock()
    db[name].find_one = AsyncMock(return_value=None)
    db[name].create_index = AsyncMock()
    db.drop_collection = AsyncMock()
    db.drop_database = AsyncMock()
    return db


def test_opt_in_guard() -> None:
    live = MagicMock(side_effect=AssertionError("live verification was called"))
    with patch.dict(globals(), {"_run_live": live}):
        assert _run_if_authorized(False) is None
    live.assert_not_called()


def test_unconfirmed_create_never_cleans() -> None:
    db = MagicMock()
    db.create_collection = AsyncMock(side_effect=RuntimeError("secret"))
    db.drop_collection = AsyncMock()
    statuses = asyncio.run(_verify(MagicMock(), db))
    db.list_collections.assert_not_called()
    db.drop_collection.assert_not_awaited()
    assert "create outcome unknown; investigate novy_verify_" in statuses[-1]
    assert "secret" not in str(statuses)


def test_unconfirmed_metadata_never_writes_or_cleans() -> None:
    name = "novy_verify_" + UUID(int=0).hex
    db = _fake_db(name, "other", uuid4())
    ids = [UUID(int=0), UUID(int=1), *[uuid4() for _ in range(6)]]
    with patch(__name__ + ".uuid4", side_effect=ids):
        statuses = asyncio.run(_verify(MagicMock(), db))
    db[name].create_index.assert_not_awaited()
    db[name].delete_one.assert_not_awaited()
    assert statuses[-1] == f"ownership unconfirmed; investigate {name}"


def test_cleanup_uses_only_exact_document_deletes() -> None:
    name, marker, owned_uuid = "novy_verify_safe", "marker", uuid4()
    db = _fake_db(name, marker, owned_uuid)
    initial = {"_id": "random-id", "run_marker": marker, "value": "initial"}
    updated = {**initial, "value": "updated"}
    result = asyncio.run(_cleanup(db, name, marker, owned_uuid, [initial, updated]))
    assert db[name].delete_one.await_args_list == [
        call(
            {"_id": "random-id", "$expr": {"$eq": ["$$ROOT", {"$literal": initial}]}},
            collation={"locale": "simple"},
        ),
        call(
            {"_id": "random-id", "$expr": {"$eq": ["$$ROOT", {"$literal": updated}]}},
            collation={"locale": "simple"},
        ),
    ]
    db[name].delete_many.assert_not_called()
    db.drop_collection.assert_not_awaited()
    db.drop_database.assert_not_awaited()
    assert result == "cleanup: synthetic records absent"


@pytest.mark.parametrize("mismatch", ["uuid", "validator"])
def test_metadata_mismatch_prevents_deletion(mismatch: str) -> None:
    name, marker, owned_uuid = "novy_verify_safe", "marker", uuid4()
    db = _fake_db(
        name,
        "other" if mismatch == "validator" else marker,
        uuid4() if mismatch == "uuid" else owned_uuid,
    )
    result = asyncio.run(_cleanup(db, name, marker, owned_uuid, [{"_id": "id"}]))
    db[name].delete_one.assert_not_awaited()
    assert result == f"cleanup inconclusive; investigate {name}"


@pytest.mark.parametrize("mismatch", ["uuid", "validator"])
def test_metadata_mismatch_prevents_writes(mismatch: str) -> None:
    name, marker, owned_uuid = "novy_verify_" + UUID(int=0).hex, UUID(int=1).hex, uuid4()
    db = _fake_db(name, marker, owned_uuid)
    cursor = db.list_collections.return_value
    original = {
        "name": name,
        "info": {"uuid": owned_uuid},
        "options": {"validator": _validator(marker)},
    }
    changed: dict[str, Any] = {
        "name": name,
        "info": {"uuid": owned_uuid},
        "options": {"validator": _validator(marker)},
    }
    if mismatch == "uuid":
        changed["info"] = {"uuid": uuid4()}
    else:
        changed["options"] = {"validator": _validator("other")}
    cursor.to_list.side_effect = [[original], [changed], [changed]]
    ids = [UUID(int=0), UUID(int=1), *[uuid4() for _ in range(6)]]
    with patch(__name__ + ".uuid4", side_effect=ids):
        statuses = asyncio.run(_verify(MagicMock(), db))
    db[name].create_index.assert_not_awaited()
    db[name].delete_one.assert_not_awaited()
    assert "unique index: failed or inconclusive" in statuses


def test_cleanup_reports_unknown_remaining_document() -> None:
    name, marker, owned_uuid = "novy_verify_safe", "marker", uuid4()
    db = _fake_db(name, marker, owned_uuid)
    db[name].find_one = AsyncMock(return_value={"_id": "id", "private": "secret"})
    result = asyncio.run(_cleanup(db, name, marker, owned_uuid, [{"_id": "id"}]))
    assert result == f"cleanup inconclusive; investigate {name}"
    assert "secret" not in result


def test_cleanup_exception_is_redacted() -> None:
    name, marker, owned_uuid = "novy_verify_safe", "marker", uuid4()
    db = _fake_db(name, marker, owned_uuid)
    db[name].delete_one.side_effect = RuntimeError("secret")
    result = asyncio.run(_cleanup(db, name, marker, owned_uuid, [{"_id": "id"}]))
    assert result == f"cleanup inconclusive; investigate {name}"
