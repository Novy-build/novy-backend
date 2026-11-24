"""MongoDB import: two-pass typed remapping with idempotency.

Accepts the frontend's {project, appData} payload, allocates fresh
ObjectIds, remaps typed references, and persists atomically.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from novy.application.import_project import (
    ImportConflictError,
    ImportPayload,
    ImportStoreError,
)
from novy.infrastructure.projects import PROJECTS_COLLECTION
from novy.infrastructure.reference_remap import remap_draft_references

APP_DATABASES_COLLECTION = "app_databases"
APP_COLLECTIONS_COLLECTION = "app_collections"
APP_RECORDS_COLLECTION = "app_records"
IDEMPOTENCY_COLLECTION = "idempotency"


def _now() -> datetime:
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _payload_digest(payload: ImportPayload) -> str:
    """Compute a stable digest of the import payload."""
    serialized = json.dumps(
        {"project": payload.project, "app_data": payload.app_data},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


class MongoImportRepository:
    """Transactional import against platform_db."""

    def __init__(self, database: AsyncDatabase[Any]) -> None:
        self._db = database

    async def import_project(
        self,
        owner_id: str,
        payload: ImportPayload,
        idempotency_key: str,
    ) -> str:
        session: AsyncClientSession | None = None
        try:
            session = self._db.client.start_session()
            async with await session.start_transaction():
                # Check idempotency
                digest = _payload_digest(payload)
                existing = await self._db[IDEMPOTENCY_COLLECTION].find_one(
                    {
                        "owner_id": owner_id,
                        "operation": "import",
                        "key": idempotency_key,
                    },
                    session=session,
                )
                if existing is not None:
                    if existing.get("digest") != digest:
                        raise ImportConflictError()
                    # Return original result
                    result_id = existing.get("result_project_id", "")
                    if not isinstance(result_id, str):
                        result_id = str(result_id)
                    return result_id

                # Pass 1: Allocate fresh IDs
                new_project_id = ObjectId()
                now = _now()

                # Map old IDs to new IDs
                db_id_map: dict[str, str] = {}
                coll_id_map: dict[str, str] = {}

                app_data = payload.app_data or {}
                databases = app_data.get("databases", [])
                collections = app_data.get("collections", [])
                records = app_data.get("records", [])

                if not isinstance(databases, list):
                    databases = []
                if not isinstance(collections, list):
                    collections = []
                if not isinstance(records, list):
                    records = []

                # Allocate new database IDs
                for db_spec in databases:
                    if isinstance(db_spec, dict) and "id" in db_spec:
                        old_id = str(db_spec["id"])
                        db_id_map[old_id] = str(ObjectId())

                # Allocate new collection IDs
                for coll_spec in collections:
                    if isinstance(coll_spec, dict) and "id" in coll_spec:
                        old_id = str(coll_spec["id"])
                        coll_id_map[old_id] = str(ObjectId())

                # Remap project draft
                project_data = payload.project
                draft_content = project_data.get("draft", {})
                if not isinstance(draft_content, dict):
                    draft_content = {}
                screens = project_data.get("screens", [])
                if not isinstance(screens, list):
                    screens = []
                draft = remap_draft_references(
                    {**draft_content, "screens": screens}, db_id_map, coll_id_map
                )

                project_doc = {
                    "_id": new_project_id,
                    "owner_id": owner_id,
                    "name": project_data.get("name", "Imported Project"),
                    "created_at": now,
                    "updated_at": now,
                    "draft": draft,
                    "schema_version": project_data.get("schemaVersion", 14),
                    "revision": 0,
                    "lifecycle_state": "active",
                    "project_type": project_data.get("projectType", "app"),
                }
                await self._db[PROJECTS_COLLECTION].insert_one(project_doc, session=session)

                # Insert databases
                for db_spec in databases:
                    if not isinstance(db_spec, dict):
                        continue
                    old_id = str(db_spec.get("id", ""))
                    new_id = db_id_map.get(old_id, str(ObjectId()))
                    db_doc = {
                        "_id": ObjectId(new_id),
                        "project_id": str(new_project_id),
                        "name": db_spec.get("name", "Database"),
                        "created_at": now,
                        "updated_at": now,
                    }
                    await self._db[APP_DATABASES_COLLECTION].insert_one(db_doc, session=session)

                # Insert collections
                for coll_spec in collections:
                    if not isinstance(coll_spec, dict):
                        continue
                    old_id = str(coll_spec.get("id", ""))
                    new_id = coll_id_map.get(old_id, str(ObjectId()))
                    old_db_id = str(coll_spec.get("databaseId", coll_spec.get("database_id", "")))
                    new_db_id = db_id_map.get(old_db_id, old_db_id)
                    coll_doc = {
                        "_id": ObjectId(new_id),
                        "project_id": str(new_project_id),
                        "database_id": new_db_id,
                        "name": coll_spec.get("name", "Collection"),
                        "fields": coll_spec.get("fields", []),
                        "record_identifier": coll_spec.get(
                            "recordIdentifier", coll_spec.get("record_identifier")
                        ),
                        "created_at": now,
                        "updated_at": now,
                    }
                    await self._db[APP_COLLECTIONS_COLLECTION].insert_one(coll_doc, session=session)

                # Insert records
                for rec_spec in records:
                    if not isinstance(rec_spec, dict):
                        continue
                    old_coll_id = str(
                        rec_spec.get("collectionId", rec_spec.get("collection_id", ""))
                    )
                    new_coll_id = coll_id_map.get(old_coll_id, old_coll_id)
                    record_doc = {
                        "_id": ObjectId(),
                        "project_id": str(new_project_id),
                        "collection_id": new_coll_id,
                        "environment": rec_spec.get("environment", "preview"),
                        "values": rec_spec.get("values", {}),
                        "created_at": now,
                        "updated_at": now,
                    }
                    await self._db[APP_RECORDS_COLLECTION].insert_one(record_doc, session=session)

                # Store idempotency record
                await self._db[IDEMPOTENCY_COLLECTION].insert_one(
                    {
                        "owner_id": owner_id,
                        "operation": "import",
                        "key": idempotency_key,
                        "digest": digest,
                        "result_project_id": str(new_project_id),
                        "created_at": now,
                    },
                    session=session,
                )

                await session.commit_transaction()
                return str(new_project_id)
        except PyMongoError as exc:
            if session is not None:
                await session.abort_transaction()
            raise ImportStoreError() from exc
        except ImportConflictError:
            if session is not None:
                await session.abort_transaction()
            raise
        except Exception as exc:
            if session is not None:
                await session.abort_transaction()
            raise ImportStoreError() from exc
        finally:
            if session is not None:
                await session.end_session()
