"""Opt-in synthetic builder session verification; never uses production collections."""

import asyncio
import logging
import secrets
import socket
import warnings
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import UUID, uuid4

import pytest
from bson import BSON
from bson.binary import UUID_SUBTYPE, Binary
from pymongo import ASCENDING, ReadPreference
from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern


def _validator(issuer: str) -> dict[str, Any]:
    return {"issuer": {"$eq": issuer}}


async def _metadata_uuid(db: Any, name: str, issuer: str) -> object | None:
    cursor = await db.list_collections(filter={"name": name})
    entries = await cursor.to_list(length=2)
    if len(entries) != 1 or entries[0].get("name") != name:
        return None
    entry = entries[0]
    if entry.get("options", {}).get("validator") != _validator(issuer):
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
    db: Any, name: str, issuer: str, owned_uuid: object, documents: list[dict[str, Any]]
) -> bool:
    try:
        collection = db[name]
        for document in documents:
            if await _metadata_uuid(db, name, issuer) != owned_uuid:
                return False
            result = await collection.delete_one(
                _exact_filter(document), collation={"locale": "simple"}
            )
            if not result.acknowledged:
                return False
        for document in documents:
            if await collection.find_one({"_id": document["_id"]}, projection={"_id": 1}):
                return False
        if await collection.find_one({}, projection={"_id": 1}) is not None:
            return False
        return await _metadata_uuid(db, name, issuer) == owned_uuid
    except Exception:
        return False


async def _verify(db: Any, other_db: Any) -> list[str]:
    from novy.domain.builder import BuilderIdentity
    from novy.domain.builder_sessions import (
        BuilderSession,
        BuilderSessionInvalid,
        ProviderTokens,
    )
    from novy.infrastructure.builder_sessions import (
        DIGEST_INDEX,
        TTL_INDEX,
        MongoBuilderSessionRepository,
        _document,
    )
    from novy.infrastructure.migrations.builder_sessions_v1 import ensure_builder_session_indexes
    from novy.infrastructure.session_security import SessionSecurity

    name = f"nvs_{uuid4().hex}"
    issuer = f"https://verify.invalid/{uuid4().hex}"
    statuses = [f"scratch collection: {name}"]
    print(statuses[0], flush=True)
    stage = "create"
    owned_uuid: object | None = None
    documents: list[dict[str, Any]] = []
    verified = False
    try:
        collection = await db.create_collection(name, validator=_validator(issuer))
        stage = "metadata"
        owned_uuid = await _metadata_uuid(db, name, issuer)
        if owned_uuid is None:
            raise ValueError

        async def confirm() -> None:
            if await _metadata_uuid(db, name, issuer) != owned_uuid:
                raise ValueError

        stage = "indexes"
        await confirm()
        await ensure_builder_session_indexes(collection)
        indexes = await collection.index_information()
        digest_index, ttl_index = indexes.get(DIGEST_INDEX, {}), indexes.get(TTL_INDEX, {})
        if (
            set(indexes) != {"_id_", DIGEST_INDEX, TTL_INDEX}
            or digest_index.get("key") != [("digest", ASCENDING)]
            or digest_index.get("unique") is not True
            or ttl_index.get("key") != [("absolute_expires_at", ASCENDING)]
            or type(ttl_index.get("expireAfterSeconds")) is not int
            or ttl_index["expireAfterSeconds"] != 0
            or any(
                index.get("sparse", False) is not False
                or index.get("hidden", False) is not False
                or "partialFilterExpression" in index
                or ("collation" in index and index["collation"].get("locale") != "simple")
                for index in (digest_index, ttl_index)
            )
            or ttl_index.get("unique", False) is not False
            or "expireAfterSeconds" in digest_index
        ):
            raise ValueError

        security = SessionSecurity(
            encryption_key=secrets.token_bytes(32),
            lookup_key=secrets.token_bytes(32),
            environment="test",
        )
        identity = BuilderIdentity(issuer, uuid4().hex)
        builder_id = uuid4().hex
        tokens = ProviderTokens(secrets.token_urlsafe(32), secrets.token_urlsafe(32))
        credential = security.generate_credential()
        now = datetime.now(UTC)
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)

        def make_session(created: datetime, browser_secret: str) -> BuilderSession:
            record_id = uuid4().hex
            return BuilderSession(
                record_id=record_id,
                digest=security.digest(browser_secret),
                identity=identity,
                builder_id=builder_id,
                tokens=security.encrypt(
                    record_id=record_id, identity=identity, builder_id=builder_id, tokens=tokens
                ),
                created_at=created,
                last_seen_at=created,
                idle_expires_at=created + timedelta(days=7),
                absolute_expires_at=created + timedelta(days=30),
                revoked_at=None,
                revision=0,
            )

        session = make_session(now, credential)
        duplicate = make_session(now, credential)
        expired = make_session(now - timedelta(days=8), security.generate_credential())
        if duplicate.record_id == session.record_id:
            raise ValueError
        documents = [_document(session), _document(duplicate), _document(expired)]
        repo = MongoBuilderSessionRepository(collection)
        other_repo = MongoBuilderSessionRepository(other_db[name])

        stage = "insert"
        await confirm()
        await repo.insert(session)
        stage = "duplicate digest"
        await confirm()
        try:
            await collection.insert_one(_document(duplicate))
        except DuplicateKeyError:
            pass
        else:
            raise ValueError
        fetched = await repo.find(session.digest)
        raw = await collection.find_one({"_id": session.record_id})
        if fetched != session or raw is None:
            raise ValueError
        encoded = bytes(BSON.encode(raw))
        if any(
            value.encode() in encoded
            for value in (tokens.access_token, tokens.refresh_token, credential)
        ):
            raise ValueError

        stage = "concurrent touch and revoke"
        await confirm()
        results = await asyncio.gather(
            repo.touch(session), other_repo.revoke(session), return_exceptions=True
        )
        if any(isinstance(result, BaseException) for result in results):
            raise ValueError
        touched, revoked = results
        if (touched is None) == (revoked is None):
            raise ValueError
        if isinstance(touched, BuilderSession):
            documents.append(_document(touched))
            if touched.revision != 1 or touched.last_seen_at < session.last_seen_at:
                raise ValueError
            stage = "revoke touched session"
            await confirm()
            revoked = await other_repo.revoke(touched)
            if not isinstance(revoked, BuilderSession):
                raise ValueError
            documents.append(_document(revoked))
            if revoked.revision != 2:
                raise ValueError
        elif isinstance(revoked, BuilderSession):
            documents.append(_document(revoked))
            if revoked.revision != 1:
                raise ValueError
            stage = "stale touch"
            await confirm()
            if await repo.touch(session) is not None:
                raise ValueError
        else:
            raise ValueError
        if (
            not isinstance(revoked, BuilderSession)
            or revoked.revoked_at is None
            or revoked.revoked_at < revoked.created_at
            or await repo.find(session.digest) != revoked
            or revoked.is_valid_at(datetime.now(UTC))
        ):
            raise ValueError
        stage = "revoked touch"
        await confirm()
        try:
            await repo.touch(revoked)
        except BuilderSessionInvalid:
            pass
        else:
            raise ValueError
        if await repo.find(session.digest) != revoked:
            raise ValueError

        stage = "idle expiry"
        await confirm()
        await repo.insert(expired)
        if await repo.find(expired.digest) != expired:
            raise ValueError
        await confirm()
        if await repo.touch(expired) is not None or await repo.find(expired.digest) != expired:
            raise ValueError

        verified = True
    except Exception:
        statuses.append(f"{stage}: failed or inconclusive; investigate {name}")
    finally:
        if owned_uuid is not None and documents:
            cleaned = await _cleanup(db, name, issuer, owned_uuid, documents)
            if verified and cleaned:
                statuses.append(
                    "synthetic records absent; collection and indexes intentionally retained"
                )
            elif verified:
                statuses.append(f"cleanup failed or inconclusive; investigate {name}")
            else:
                statuses.append(
                    f"cleanup attempted after failure; outcome inconclusive; investigate {name}"
                )
    return statuses


async def _run_live() -> list[str]:
    stage = "configuration or app startup"
    statuses: list[str] = []
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
            if (
                getattr(app.state, "mongo_client", None) is None
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
                    read_preference=ReadPreference.PRIMARY,
                    write_concern=WriteConcern(w="majority"),
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
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return asyncio.run(_run_live())
    except Exception:
        return ["verification execution: failed or inconclusive"]
    finally:
        logging.disable(level)


def test_builder_sessions_live(request: pytest.FixtureRequest) -> None:
    statuses = _run_if_authorized(request.config.getoption("--verify-builder-sessions-db"))
    if statuses is None:
        pytest.skip("pass --verify-builder-sessions-db to authorize scratch writes")
    for status in statuses:
        if not status.startswith("scratch collection: "):
            print(status)
    if not statuses or statuses[-1] != (
        "synthetic records absent; collection and indexes intentionally retained"
    ):
        pytest.fail(
            "builder session verification failed or inconclusive; see safe stages", pytrace=False
        )


def _fake_db(name: str, issuer: str, owned_uuid: UUID) -> MagicMock:
    db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(
        return_value=[
            {
                "name": name,
                "info": {"uuid": owned_uuid},
                "options": {"validator": _validator(issuer)},
            }
        ]
    )
    db.list_collections = AsyncMock(return_value=cursor)
    db[name].delete_one = AsyncMock(return_value=MagicMock(acknowledged=True))
    db[name].find_one = AsyncMock(return_value=None)
    return db


def test_opt_in_guard() -> None:
    with patch(__name__ + "._run_live") as live:
        assert _run_if_authorized(False) is None
    live.assert_not_called()


def test_exact_cleanup_only() -> None:
    name, issuer, owned_uuid = "nvs_safe", "issuer", uuid4()
    db = _fake_db(name, issuer, owned_uuid)
    documents = [{"_id": "id", "revision": 0}, {"_id": "id", "revision": 1}]
    assert asyncio.run(_cleanup(db, name, issuer, owned_uuid, documents))
    assert db[name].delete_one.await_args_list == [
        call(_exact_filter(document), collation={"locale": "simple"}) for document in documents
    ]
    db[name].delete_many.assert_not_called()
    db.drop_collection.assert_not_called()
    db.drop_database.assert_not_called()


@pytest.mark.parametrize("mismatch", ["missing", "uuid", "validator"])
def test_metadata_mismatch_prevents_delete(mismatch: str) -> None:
    name, issuer, owned_uuid = "nvs_safe", "issuer", uuid4()
    db = _fake_db(name, "other" if mismatch == "validator" else issuer, owned_uuid)
    if mismatch == "missing":
        db.list_collections.return_value.to_list.return_value = []
    elif mismatch == "uuid":
        db = _fake_db(name, issuer, uuid4())
    assert not asyncio.run(_cleanup(db, name, issuer, owned_uuid, [{"_id": "id"}]))
    db[name].delete_one.assert_not_awaited()


def test_unknown_document_left_and_cleanup_inconclusive() -> None:
    name, issuer, owned_uuid = "nvs_safe", "issuer", uuid4()
    db = _fake_db(name, issuer, owned_uuid)
    db[name].find_one.return_value = {"_id": "foreign", "private": "secret"}
    assert not asyncio.run(_cleanup(db, name, issuer, owned_uuid, [{"_id": "id"}]))
    assert db[name].delete_one.await_args_list == [
        call(_exact_filter({"_id": "id"}), collation={"locale": "simple"})
    ]


def test_errors_redacted_and_unknown_create_not_cleaned(capsys: pytest.CaptureFixture[str]) -> None:
    db = MagicMock()
    db.create_collection = AsyncMock(side_effect=RuntimeError("secret URI"))
    statuses = asyncio.run(_verify(db, MagicMock()))
    assert "failed or inconclusive" in statuses[-1]
    assert "secret" not in str(statuses) + capsys.readouterr().out
    db.list_collections.assert_not_called()
    db.__getitem__.assert_not_called()


def test_cleanup_error_redacted() -> None:
    name, issuer, owned_uuid = "nvs_safe", "issuer", uuid4()
    db = _fake_db(name, issuer, owned_uuid)
    db[name].delete_one.side_effect = RuntimeError("secret")
    assert not asyncio.run(_cleanup(db, name, issuer, owned_uuid, [{"_id": "id"}]))
    assert db[name].delete_one.await_count == 1


@pytest.fixture(autouse=True)
def _offline_network_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.config.getoption("--verify-builder-sessions-db") and request.node.name == (
        "test_builder_sessions_live"
    ):
        return

    def reject(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("network disabled")

    monkeypatch.setattr(socket, "getaddrinfo", reject)
    monkeypatch.setattr(socket, "create_connection", reject)
