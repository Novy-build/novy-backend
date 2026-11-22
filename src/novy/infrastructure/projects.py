"""MongoDB project repository implementation.

Implements the ``ProjectRepository`` port against ``platform_db.projects``.
All reads and writes are owner-scoped; project existence is never leaked
across owners.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.codec_options import CodecOptions
from bson.errors import BSONError
from pymongo import ASCENDING, ReadPreference
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from novy.application.projects import ProjectStoreError
from novy.domain.projects import (
    LIFECYCLE_DELETED,
    Project,
    ProjectConflictError,
    ProjectDataError,
    ProjectDraft,
    ProjectNotFoundError,
    ProjectPreconditionRequiredError,
    ProjectRevision,
)
from novy.infrastructure.bson_document import StrictDocument as _StrictDocument

PROJECTS_COLLECTION = "projects"
PROJECTS_OWNER_UPDATED_INDEX = "projects_owner_updated_v1"
PROJECTS_CAS_INDEX = "projects_owner_id_revision_v1"

_MAX_TIME_MS = 5000


def _now() -> datetime:
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _to_object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception as exc:
        raise ProjectDataError() from exc


class MongoProjectRepository:
    """Owner-scoped MongoDB implementation of ProjectRepository."""

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

    async def _require_indexes(self) -> None:
        valid = False
        try:
            indexes = await self._write_collection.index_information()
            index = indexes.get(PROJECTS_OWNER_UPDATED_INDEX)
            if isinstance(index, dict):
                valid = (
                    index.get("key")
                    == [("owner_id", ASCENDING), ("updated_at", ASCENDING), ("_id", ASCENDING)]
                    and index.get("hidden", False) is False
                )
        except Exception:
            pass
        if not valid:
            raise ProjectStoreError() from None

    def _document_to_project(self, document: dict[str, Any]) -> Project:
        try:
            project_id = str(document["_id"])
            owner_id = document["owner_id"]
            name = document["name"]
            created_at = document["created_at"]
            updated_at = document["updated_at"]
            draft_content = document.get("draft", {})
            schema_version = document.get("schema_version", 14)
            revision_value = document.get("revision", 0)
            lifecycle_state = document.get("lifecycle_state", "active")
            project_type = document.get("project_type", "app")
            if not isinstance(draft_content, dict):
                raise ValueError("Invalid draft")
            return Project(
                id=project_id,
                owner_id=owner_id,
                name=name,
                created_at=created_at,
                updated_at=updated_at,
                draft=ProjectDraft(content=draft_content),
                schema_version=schema_version,
                revision=ProjectRevision(value=revision_value),
                lifecycle_state=lifecycle_state,
                project_type=project_type,
            )
        except (KeyError, TypeError, ValueError):
            raise ProjectDataError() from None

    def _project_to_document(self, project: Project) -> dict[str, Any]:
        return {
            "_id": _to_object_id(project.id),
            "owner_id": project.owner_id,
            "name": project.name,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "draft": project.draft.content,
            "schema_version": project.schema_version,
            "revision": project.revision.value,
            "lifecycle_state": project.lifecycle_state,
            "project_type": project.project_type,
        }

    async def create(
        self, owner_id: str, name: str, draft: ProjectDraft, *, project_type: str = "app"
    ) -> Project:
        await self._require_indexes()
        now = _now()
        project_id = ObjectId()
        project = Project(
            id=str(project_id),
            owner_id=owner_id,
            name=name,
            created_at=now,
            updated_at=now,
            draft=draft,
            schema_version=14,
            revision=ProjectRevision(value=0),
            lifecycle_state="active",
            project_type=project_type,
        )
        document = self._project_to_document(project)
        try:
            await self._write_collection.insert_one(document)
        except (PyMongoError, BSONError, ValueError, TypeError):
            raise ProjectStoreError() from None
        return project

    async def find_by_id_and_owner(self, project_id: str, owner_id: str) -> Project | None:
        await self._require_indexes()
        try:
            oid = _to_object_id(project_id)
        except ProjectDataError:
            return None
        document = None
        try:
            document = await self._read_collection.find_one(
                {"_id": oid, "owner_id": owner_id},
                max_time_ms=_MAX_TIME_MS,
            )
        except (PyMongoError, BSONError):
            raise ProjectStoreError() from None
        if document is None:
            return None
        try:
            return self._document_to_project(document)
        except ProjectDataError:
            raise

    async def find_by_owner(
        self, owner_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> list[Project]:
        await self._require_indexes()
        query: dict[str, Any] = {"owner_id": owner_id, "lifecycle_state": {"$ne": "deleted"}}
        if cursor is not None:
            try:
                cursor_oid = _to_object_id(cursor)
                query["_id"] = {"$gt": cursor_oid}
            except ProjectDataError:
                pass
        try:
            cursor_result = (
                self._read_collection.find(query, max_time_ms=_MAX_TIME_MS)
                .sort([("updated_at", -1), ("_id", 1)])
                .limit(limit)
            )
            documents = await cursor_result.to_list(length=limit)
        except (PyMongoError, BSONError):
            raise ProjectStoreError() from None
        projects: list[Project] = []
        for doc in documents:
            try:
                projects.append(self._document_to_project(doc))
            except ProjectDataError:
                continue
        return projects

    async def save_with_revision(
        self,
        project_id: str,
        owner_id: str,
        expected_revision: int,
        new_draft: ProjectDraft,
    ) -> Project:
        await self._require_indexes()
        try:
            oid = _to_object_id(project_id)
        except ProjectDataError:
            raise ProjectNotFoundError() from None

        # First check existence without revealing across owners
        existing = await self.find_by_id_and_owner(project_id, owner_id)
        if existing is None:
            raise ProjectNotFoundError()

        if existing.lifecycle_state == LIFECYCLE_DELETED:
            raise ProjectNotFoundError()

        # Missing precondition check
        if expected_revision is None:
            raise ProjectPreconditionRequiredError()

        new_revision_value = existing.revision.value + 1
        now = _now()
        filter_doc = {
            "_id": oid,
            "owner_id": owner_id,
            "lifecycle_state": {"$ne": LIFECYCLE_DELETED},
            "revision": expected_revision,
        }
        update_doc = {
            "$set": {
                "draft": new_draft.content,
                "updated_at": now,
                "revision": new_revision_value,
            }
        }
        try:
            result = await self._write_collection.find_one_and_update(
                filter_doc,
                update_doc,
                return_document=True,
                max_time_ms=_MAX_TIME_MS,
            )
        except (PyMongoError, BSONError):
            raise ProjectStoreError() from None

        if result is None:
            # Could be a revision mismatch or deletion race
            # Re-check to distinguish
            current = await self.find_by_id_and_owner(project_id, owner_id)
            if current is None or current.lifecycle_state == LIFECYCLE_DELETED:
                raise ProjectNotFoundError()
            if current.revision.value != expected_revision:
                raise ProjectConflictError()
            raise ProjectStoreError() from None

        return self._document_to_project(result)

    async def rename(self, project_id: str, owner_id: str, new_name: str) -> Project:
        await self._require_indexes()
        existing = await self.find_by_id_and_owner(project_id, owner_id)
        if existing is None or existing.lifecycle_state == LIFECYCLE_DELETED:
            raise ProjectNotFoundError()

        try:
            oid = _to_object_id(project_id)
        except ProjectDataError:
            raise ProjectNotFoundError() from None

        now = _now()
        try:
            result = await self._write_collection.find_one_and_update(
                {"_id": oid, "owner_id": owner_id, "lifecycle_state": {"$ne": LIFECYCLE_DELETED}},
                {"$set": {"name": new_name, "updated_at": now}},
                return_document=True,
                max_time_ms=_MAX_TIME_MS,
            )
        except (PyMongoError, BSONError):
            raise ProjectStoreError() from None

        if result is None:
            raise ProjectNotFoundError()
        return self._document_to_project(result)

    async def delete(self, project_id: str, owner_id: str) -> None:
        await self._require_indexes()
        existing = await self.find_by_id_and_owner(project_id, owner_id)
        if existing is None:
            # Non-revealing: don't distinguish not-found from already-deleted
            return
        if existing.lifecycle_state == LIFECYCLE_DELETED:
            return

        try:
            oid = _to_object_id(project_id)
        except ProjectDataError:
            return

        now = _now()
        try:
            await self._write_collection.update_one(
                {"_id": oid, "owner_id": owner_id},
                {"$set": {"lifecycle_state": LIFECYCLE_DELETED, "updated_at": now}},
            )
        except (PyMongoError, BSONError):
            raise ProjectStoreError() from None

    async def exists(self, project_id: str, owner_id: str) -> bool:
        await self._require_indexes()
        try:
            oid = _to_object_id(project_id)
        except ProjectDataError:
            return False
        try:
            count = await self._read_collection.count_documents(
                {"_id": oid, "owner_id": owner_id},
                max_time_ms=_MAX_TIME_MS,
            )
        except (PyMongoError, BSONError):
            raise ProjectStoreError() from None
        return count > 0
