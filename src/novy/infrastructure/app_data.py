"""MongoDB application data repository implementation.

Implements the AppDatabaseRepository, AppCollectionRepository, and
AppRecordRepository ports against platform_db collections.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.codec_options import CodecOptions
from bson.errors import BSONError
from pymongo import ReadPreference
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from novy.application.app_data import (
    AppCollectionRepository,
    AppDatabaseRepository,
    AppDataStoreError,
    AppRecordRepository,
)
from novy.domain.app_data import AppCollection, AppDatabase, AppDataError, AppRecord
from novy.infrastructure.bson_document import StrictDocument as _StrictDocument

APP_DATABASES_COLLECTION = "app_databases"
APP_COLLECTIONS_COLLECTION = "app_collections"
APP_RECORDS_COLLECTION = "app_records"

_MAX_TIME_MS = 5000


def _now() -> datetime:
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _to_object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception as exc:
        raise AppDataError() from exc


class _BaseRepo:
    def __init__(self, collection: AsyncCollection[dict[str, Any]]) -> None:
        self._write_collection: AsyncCollection[dict[str, Any]] = collection.with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=UTC),
            write_concern=WriteConcern(w="majority"),
            read_preference=ReadPreference.PRIMARY,
        )
        self._read_collection = self._write_collection.with_options(
            codec_options=CodecOptions(document_class=_StrictDocument, tz_aware=True, tzinfo=UTC),
            read_preference=ReadPreference.PRIMARY,
            read_concern=ReadConcern("linearizable"),
        )


class MongoAppDatabaseRepository(_BaseRepo, AppDatabaseRepository):
    async def create(self, project_id: str, name: str) -> AppDatabase:
        now = _now()
        oid = ObjectId()
        doc = {
            "_id": oid,
            "project_id": project_id,
            "name": name,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self._write_collection.insert_one(doc)
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        return AppDatabase(
            id=str(oid),
            project_id=project_id,
            name=name,
            created_at=now,
            updated_at=now,
        )

    async def find_by_id_and_project(self, database_id: str, project_id: str) -> AppDatabase | None:
        try:
            oid = _to_object_id(database_id)
        except AppDataError:
            return None
        try:
            doc = await self._read_collection.find_one(
                {"_id": oid, "project_id": project_id}, max_time_ms=_MAX_TIME_MS
            )
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        if doc is None:
            return None
        return AppDatabase(
            id=str(doc["_id"]),
            project_id=doc["project_id"],
            name=doc["name"],
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )

    async def find_by_project(self, project_id: str) -> list[AppDatabase]:
        try:
            cursor = self._read_collection.find(
                {"project_id": project_id}, max_time_ms=_MAX_TIME_MS
            )
            docs = await cursor.to_list(length=1000)
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        result: list[AppDatabase] = []
        for doc in docs:
            result.append(
                AppDatabase(
                    id=str(doc["_id"]),
                    project_id=doc["project_id"],
                    name=doc["name"],
                    created_at=doc["created_at"],
                    updated_at=doc["updated_at"],
                )
            )
        return result

    async def delete(self, database_id: str, project_id: str) -> None:
        try:
            oid = _to_object_id(database_id)
        except AppDataError:
            return
        try:
            await self._write_collection.delete_one({"_id": oid, "project_id": project_id})
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None


class MongoAppCollectionRepository(_BaseRepo, AppCollectionRepository):
    async def create(
        self,
        project_id: str,
        database_id: str,
        name: str,
        fields: list[dict[str, Any]],
        record_identifier: str | None = None,
    ) -> AppCollection:
        now = _now()
        oid = ObjectId()
        doc = {
            "_id": oid,
            "project_id": project_id,
            "database_id": database_id,
            "name": name,
            "fields": fields,
            "record_identifier": record_identifier,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self._write_collection.insert_one(doc)
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        return AppCollection(
            id=str(oid),
            project_id=project_id,
            database_id=database_id,
            name=name,
            fields=fields,
            record_identifier=record_identifier,
            created_at=now,
            updated_at=now,
        )

    async def find_by_id_and_project(
        self, collection_id: str, project_id: str
    ) -> AppCollection | None:
        try:
            oid = _to_object_id(collection_id)
        except AppDataError:
            return None
        try:
            doc = await self._read_collection.find_one(
                {"_id": oid, "project_id": project_id}, max_time_ms=_MAX_TIME_MS
            )
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        if doc is None:
            return None
        return AppCollection(
            id=str(doc["_id"]),
            project_id=doc["project_id"],
            database_id=doc["database_id"],
            name=doc["name"],
            fields=doc.get("fields", []),
            record_identifier=doc.get("record_identifier"),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )

    async def find_by_project(
        self, project_id: str, database_id: str | None = None
    ) -> list[AppCollection]:
        query: dict[str, Any] = {"project_id": project_id}
        if database_id is not None:
            query["database_id"] = database_id
        try:
            cursor = self._read_collection.find(query, max_time_ms=_MAX_TIME_MS)
            docs = await cursor.to_list(length=1000)
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        result: list[AppCollection] = []
        for doc in docs:
            result.append(
                AppCollection(
                    id=str(doc["_id"]),
                    project_id=doc["project_id"],
                    database_id=doc["database_id"],
                    name=doc["name"],
                    fields=doc.get("fields", []),
                    record_identifier=doc.get("record_identifier"),
                    created_at=doc["created_at"],
                    updated_at=doc["updated_at"],
                )
            )
        return result

    async def update(self, collection_id: str, project_id: str, **fields: object) -> AppCollection:
        try:
            oid = _to_object_id(collection_id)
        except AppDataError:
            raise AppDataStoreError() from None
        now = _now()
        update_set: dict[str, Any] = {"updated_at": now}
        for key, value in fields.items():
            update_set[key] = value
        try:
            result = await self._write_collection.find_one_and_update(
                {"_id": oid, "project_id": project_id},
                {"$set": update_set},
                return_document=True,
                max_time_ms=_MAX_TIME_MS,
            )
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        if result is None:
            raise AppDataStoreError() from None
        return AppCollection(
            id=str(result["_id"]),
            project_id=result["project_id"],
            database_id=result["database_id"],
            name=result["name"],
            fields=result.get("fields", []),
            record_identifier=result.get("record_identifier"),
            created_at=result["created_at"],
            updated_at=result["updated_at"],
        )

    async def delete(self, collection_id: str, project_id: str) -> None:
        try:
            oid = _to_object_id(collection_id)
        except AppDataError:
            return
        try:
            await self._write_collection.delete_one({"_id": oid, "project_id": project_id})
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None


class MongoAppRecordRepository(_BaseRepo, AppRecordRepository):
    async def create(
        self, project_id: str, collection_id: str, environment: str, values: dict[str, Any]
    ) -> AppRecord:
        now = _now()
        oid = ObjectId()
        doc = {
            "_id": oid,
            "project_id": project_id,
            "collection_id": collection_id,
            "environment": environment,
            "values": values,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self._write_collection.insert_one(doc)
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        return AppRecord(
            id=str(oid),
            project_id=project_id,
            collection_id=collection_id,
            environment=environment,
            values=values,
            created_at=now,
            updated_at=now,
        )

    async def find_by_id_and_scope(
        self, record_id: str, project_id: str, collection_id: str, environment: str
    ) -> AppRecord | None:
        try:
            oid = _to_object_id(record_id)
        except AppDataError:
            return None
        try:
            doc = await self._read_collection.find_one(
                {
                    "_id": oid,
                    "project_id": project_id,
                    "collection_id": collection_id,
                    "environment": environment,
                },
                max_time_ms=_MAX_TIME_MS,
            )
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        if doc is None:
            return None
        return AppRecord(
            id=str(doc["_id"]),
            project_id=doc["project_id"],
            collection_id=doc["collection_id"],
            environment=doc["environment"],
            values=doc.get("values", {}),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )

    async def find_by_scope(
        self, project_id: str, collection_id: str, environment: str
    ) -> list[AppRecord]:
        try:
            cursor = self._read_collection.find(
                {
                    "project_id": project_id,
                    "collection_id": collection_id,
                    "environment": environment,
                },
                max_time_ms=_MAX_TIME_MS,
            )
            docs = await cursor.to_list(length=1000)
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        result: list[AppRecord] = []
        for doc in docs:
            result.append(
                AppRecord(
                    id=str(doc["_id"]),
                    project_id=doc["project_id"],
                    collection_id=doc["collection_id"],
                    environment=doc["environment"],
                    values=doc.get("values", {}),
                    created_at=doc["created_at"],
                    updated_at=doc["updated_at"],
                )
            )
        return result

    async def update(
        self,
        record_id: str,
        project_id: str,
        collection_id: str,
        environment: str,
        values: dict[str, Any],
    ) -> AppRecord:
        try:
            oid = _to_object_id(record_id)
        except AppDataError:
            raise AppDataStoreError() from None
        now = _now()
        try:
            result = await self._write_collection.find_one_and_update(
                {
                    "_id": oid,
                    "project_id": project_id,
                    "collection_id": collection_id,
                    "environment": environment,
                },
                {"$set": {"values": values, "updated_at": now}},
                return_document=True,
                max_time_ms=_MAX_TIME_MS,
            )
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
        if result is None:
            raise AppDataStoreError() from None
        return AppRecord(
            id=str(result["_id"]),
            project_id=result["project_id"],
            collection_id=result["collection_id"],
            environment=result["environment"],
            values=result.get("values", {}),
            created_at=result["created_at"],
            updated_at=result["updated_at"],
        )

    async def delete(
        self, record_id: str, project_id: str, collection_id: str, environment: str
    ) -> None:
        try:
            oid = _to_object_id(record_id)
        except AppDataError:
            return
        try:
            await self._write_collection.delete_one(
                {
                    "_id": oid,
                    "project_id": project_id,
                    "collection_id": collection_id,
                    "environment": environment,
                }
            )
        except (PyMongoError, BSONError):
            raise AppDataStoreError() from None
