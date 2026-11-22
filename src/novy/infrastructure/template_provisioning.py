"""MongoDB transactional template provisioning.

Commits project, databases, collections, and sample records together in a
single MongoDB transaction. Rolls back everything on failure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from novy.application.template_provisioning import (
    TemplateBlueprint,
    TemplateProvisioningError,
)
from novy.domain.projects import ProjectDraft
from novy.infrastructure.projects import PROJECTS_COLLECTION


def _now() -> datetime:
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


class MongoTemplateProvisioningStore:
    """Transactional provisioning against platform_db."""

    def __init__(self, database: AsyncDatabase[Any]) -> None:
        self._db = database

    async def provision_template(self, owner_id: str, blueprint: TemplateBlueprint) -> str:
        session: AsyncClientSession | None = None
        try:
            session = self._db.client.start_session()
            async with await session.start_transaction():
                project_id = ObjectId()
                now = _now()
                draft = ProjectDraft(content={"screens": list(blueprint.screens)})
                project_doc = {
                    "_id": project_id,
                    "owner_id": owner_id,
                    "name": blueprint.name,
                    "created_at": now,
                    "updated_at": now,
                    "draft": draft.content,
                    "schema_version": 14,
                    "revision": 0,
                    "lifecycle_state": "active",
                    "project_type": blueprint.project_type,
                }
                await self._db[PROJECTS_COLLECTION].insert_one(project_doc, session=session)

                # Provision databases, collections, and records
                for db_spec in blueprint.databases:
                    db_doc = {
                        "_id": ObjectId(),
                        "project_id": str(project_id),
                        "name": db_spec.name,
                        "created_at": now,
                        "updated_at": now,
                    }
                    await self._db["app_databases"].insert_one(db_doc, session=session)
                    db_id = str(db_doc["_id"])

                    for coll_spec in db_spec.collections:
                        coll_doc = {
                            "_id": ObjectId(),
                            "project_id": str(project_id),
                            "database_id": db_id,
                            "name": coll_spec.name,
                            "fields": coll_spec.fields,
                            "created_at": now,
                            "updated_at": now,
                        }
                        await self._db["app_collections"].insert_one(coll_doc, session=session)
                        coll_id = str(coll_doc["_id"])

                        for record_spec in coll_spec.records:
                            record_doc = {
                                "_id": ObjectId(),
                                "project_id": str(project_id),
                                "collection_id": coll_id,
                                "environment": "preview",
                                "values": record_spec.get("values", {}),
                                "created_at": now,
                                "updated_at": now,
                            }
                            await self._db["app_records"].insert_one(record_doc, session=session)

                await session.commit_transaction()
                return str(project_id)
        except PyMongoError as exc:
            if session is not None:
                await session.abort_transaction()
            raise TemplateProvisioningError(str(exc)) from exc
        except Exception as exc:
            if session is not None:
                await session.abort_transaction()
            raise TemplateProvisioningError(str(exc)) from exc
        finally:
            if session is not None:
                await session.end_session()
