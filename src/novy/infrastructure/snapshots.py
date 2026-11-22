"""MongoDB snapshot repository implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.codec_options import CodecOptions
from pymongo import ReadPreference
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from novy.application.snapshots import (
    SnapshotStoreError,
)
from novy.domain.snapshots import PrototypeSnapshot
from novy.infrastructure.bson_document import StrictDocument as _StrictDocument

SNAPSHOTS_COLLECTION = "snapshots"
SNAPSHOTS_INDEX = "snapshots_project_env_v1"
ACTIVE_SNAPSHOTS_COLLECTION = "active_snapshots"

_MAX_TIME_MS = 5000


def _now() -> datetime:
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


class MongoSnapshotRepository:
    """MongoDB implementation of SnapshotRepository."""

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
        self._active_collection: AsyncCollection[dict[str, Any]] = collection.database[
            ACTIVE_SNAPSHOTS_COLLECTION
        ].with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=UTC),
            write_concern=WriteConcern(w="majority"),
            read_preference=ReadPreference.PRIMARY,
        )

    async def create(self, snapshot: PrototypeSnapshot) -> PrototypeSnapshot:
        oid = ObjectId()
        now = _now()
        doc = {
            "_id": oid,
            "project_id": snapshot.project_id,
            "environment": snapshot.environment,
            "schema_version": snapshot.schema_version,
            "source_revision": snapshot.source_revision,
            "renderer_version": snapshot.renderer_version,
            "capability_version": snapshot.capability_version,
            "project_document": snapshot.project_document,
            "data_schemas": snapshot.data_schemas,
            "policies": snapshot.policies,
            "created_at": now,
            "created_by": snapshot.created_by,
        }
        try:
            await self._write_collection.insert_one(doc)
        except (PyMongoError, Exception):
            raise SnapshotStoreError() from None
        return PrototypeSnapshot(
            id=str(oid),
            project_id=snapshot.project_id,
            environment=snapshot.environment,
            schema_version=snapshot.schema_version,
            source_revision=snapshot.source_revision,
            renderer_version=snapshot.renderer_version,
            capability_version=snapshot.capability_version,
            project_document=snapshot.project_document,
            data_schemas=snapshot.data_schemas,
            policies=snapshot.policies,
            created_at=now,
            created_by=snapshot.created_by,
        )

    async def find_by_id(self, snapshot_id: str, project_id: str) -> PrototypeSnapshot | None:
        try:
            oid = ObjectId(snapshot_id)
        except Exception:
            return None
        try:
            doc = await self._read_collection.find_one(
                {"_id": oid, "project_id": project_id},
                max_time_ms=_MAX_TIME_MS,
            )
        except (PyMongoError, Exception):
            raise SnapshotStoreError() from None
        if doc is None:
            return None
        return self._doc_to_snapshot(doc)

    async def find_active(self, project_id: str, environment: str) -> PrototypeSnapshot | None:
        try:
            active_doc = await self._active_collection.find_one(
                {"project_id": project_id, "environment": environment},
                max_time_ms=_MAX_TIME_MS,
            )
        except (PyMongoError, Exception):
            raise SnapshotStoreError() from None
        if active_doc is None:
            return None
        snapshot_id = active_doc.get("snapshot_id")
        if not isinstance(snapshot_id, str):
            return None
        return await self.find_by_id(snapshot_id, project_id)

    async def set_active(self, project_id: str, environment: str, snapshot_id: str) -> None:
        try:
            await self._active_collection.update_one(
                {"project_id": project_id, "environment": environment},
                {"$set": {"snapshot_id": snapshot_id, "updated_at": _now()}},
                upsert=True,
            )
        except (PyMongoError, Exception):
            raise SnapshotStoreError() from None

    def _doc_to_snapshot(self, doc: dict[str, Any]) -> PrototypeSnapshot:
        return PrototypeSnapshot(
            id=str(doc["_id"]),
            project_id=doc["project_id"],
            environment=doc["environment"],
            schema_version=doc["schema_version"],
            source_revision=doc["source_revision"],
            renderer_version=doc["renderer_version"],
            capability_version=doc["capability_version"],
            project_document=doc.get("project_document", {}),
            data_schemas=doc.get("data_schemas", []),
            policies=doc.get("policies", []),
            created_at=doc["created_at"],
            created_by=doc["created_by"],
        )
