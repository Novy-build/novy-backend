"""MongoDB duplication: schema-only and copy-records.

Copies a project with fresh persisted ObjectIds, remaps typed references,
and optionally copies records. All within a transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from novy.application.duplicate import DuplicateStoreError
from novy.infrastructure.projects import PROJECTS_COLLECTION

APP_DATABASES_COLLECTION = "app_databases"
APP_COLLECTIONS_COLLECTION = "app_collections"
APP_RECORDS_COLLECTION = "app_records"


def _now() -> datetime:
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


class MongoDuplicateRepository:
    """Transactional duplication against platform_db."""

    def __init__(self, database: AsyncDatabase[Any]) -> None:
        self._db = database

    async def duplicate_schema(
        self,
        source_project_id: str,
        owner_id: str,
        new_name: str,
        *,
        copy_records: bool = False,
    ) -> str:
        session: AsyncClientSession | None = None
        try:
            session = self._db.client.start_session()
            async with await session.start_transaction():
                # 1. Find source project
                source_doc = await self._db[PROJECTS_COLLECTION].find_one(
                    {"_id": ObjectId(source_project_id), "owner_id": owner_id},
                    session=session,
                )
                if source_doc is None:
                    raise DuplicateStoreError()

                # 2. Create new project with fresh ID (draft remapped after copy)
                new_project_id = ObjectId()
                now = _now()
                draft = source_doc.get("draft", {})
                new_project_doc = {
                    "_id": new_project_id,
                    "owner_id": owner_id,
                    "name": new_name,
                    "created_at": now,
                    "updated_at": now,
                    "draft": draft,
                    "schema_version": source_doc.get("schema_version", 14),
                    "revision": 0,
                    "lifecycle_state": "active",
                    "project_type": source_doc.get("project_type", "app"),
                }
                await self._db[PROJECTS_COLLECTION].insert_one(new_project_doc, session=session)

                # 3. Copy databases and collections
                db_id_map: dict[str, str] = {}
                coll_id_map: dict[str, str] = {}
                source_dbs = (
                    await self._db[APP_DATABASES_COLLECTION]
                    .find({"project_id": source_project_id}, session=session)
                    .to_list(length=1000)
                )

                for db_doc in source_dbs:
                    old_db_id = str(db_doc["_id"])
                    new_db_id = ObjectId()
                    db_id_map[old_db_id] = str(new_db_id)
                    new_db_doc = {
                        "_id": new_db_id,
                        "project_id": str(new_project_id),
                        "name": db_doc["name"],
                        "created_at": now,
                        "updated_at": now,
                    }
                    await self._db[APP_DATABASES_COLLECTION].insert_one(new_db_doc, session=session)

                    # Copy collections for this database
                    source_colls = (
                        await self._db[APP_COLLECTIONS_COLLECTION]
                        .find(
                            {"project_id": source_project_id, "database_id": old_db_id},
                            session=session,
                        )
                        .to_list(length=1000)
                    )

                    for coll_doc in source_colls:
                        old_coll_id = str(coll_doc["_id"])
                        new_coll_id = ObjectId()
                        coll_id_map[old_coll_id] = str(new_coll_id)
                        new_coll_doc = {
                            "_id": new_coll_id,
                            "project_id": str(new_project_id),
                            "database_id": db_id_map[old_db_id],
                            "name": coll_doc["name"],
                            "fields": coll_doc.get("fields", []),
                            "record_identifier": coll_doc.get("record_identifier"),
                            "created_at": now,
                            "updated_at": now,
                        }
                        await self._db[APP_COLLECTIONS_COLLECTION].insert_one(
                            new_coll_doc, session=session
                        )

                        # Copy records if requested
                        if copy_records:
                            source_records = (
                                await self._db[APP_RECORDS_COLLECTION]
                                .find(
                                    {
                                        "project_id": source_project_id,
                                        "collection_id": old_coll_id,
                                    },
                                    session=session,
                                )
                                .to_list(length=10000)
                            )
                            for rec_doc in source_records:
                                new_rec_doc = {
                                    "_id": ObjectId(),
                                    "project_id": str(new_project_id),
                                    "collection_id": str(new_coll_id),
                                    "environment": rec_doc.get("environment", "preview"),
                                    "values": rec_doc.get("values", {}),
                                    "created_at": now,
                                    "updated_at": now,
                                }
                                await self._db[APP_RECORDS_COLLECTION].insert_one(
                                    new_rec_doc, session=session
                                )

                # Remap draft with new IDs
                remapped_draft = _remap_draft(draft, db_id_map, coll_id_map)
                await self._db[PROJECTS_COLLECTION].update_one(
                    {"_id": new_project_id},
                    {"$set": {"draft": remapped_draft, "updated_at": now}},
                    session=session,
                )

                await session.commit_transaction()
                return str(new_project_id)
        except PyMongoError as exc:
            if session is not None:
                await session.abort_transaction()
            raise DuplicateStoreError() from exc
        except Exception as exc:
            if session is not None:
                await session.abort_transaction()
            raise DuplicateStoreError() from exc
        finally:
            if session is not None:
                await session.end_session()


def _remap_draft(
    draft: dict[str, Any], db_id_map: dict[str, str], coll_id_map: dict[str, str]
) -> dict[str, Any]:
    """Remap typed references in the draft.

    Rewrites known collection/database references using the ID maps.
    Preserves arbitrary text, dynamic expressions (e.g. ``$recordId``),
    and unknown fields. Concrete record IDs are NOT remapped unless
    the referenced record was copied (handled separately).
    """
    import copy

    result = copy.deepcopy(draft)

    def _remap_value(value: Any) -> Any:
        if isinstance(value, dict):
            new: dict[str, Any] = {}
            for key, val in value.items():
                # Remap known collection reference fields
                if key in (
                    "collectionId",
                    "collection_id",
                    "listSourceCollectionId",
                ) and isinstance(val, str):
                    new[key] = coll_id_map.get(val, val)
                elif key in ("databaseId", "database_id") and isinstance(val, str):
                    new[key] = db_id_map.get(val, val)
                else:
                    new[key] = _remap_value(val)
            return new
        if isinstance(value, list):
            return [_remap_value(item) for item in value]
        # Preserve strings as-is (including dynamic expressions like $recordId
        # and arbitrary ID-looking text)
        return value

    remapped = _remap_value(result)
    if not isinstance(remapped, dict):
        return result
    return remapped
