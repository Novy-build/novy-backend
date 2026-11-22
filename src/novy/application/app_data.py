"""Application data persistence ports.

Defines repository protocols for databases, collections, and records.
All operations are owner/project/environment-scoped.
"""

from __future__ import annotations

from typing import Protocol

from novy.domain.app_data import AppCollection, AppDatabase, AppRecord


class AppDataStoreError(Exception):
    """Raised when application data storage is unavailable."""

    def __init__(self) -> None:
        super().__init__("Application data storage is unavailable")


class AppDatabaseRepository(Protocol):
    async def create(self, project_id: str, name: str) -> AppDatabase: ...

    async def find_by_id_and_project(
        self, database_id: str, project_id: str
    ) -> AppDatabase | None: ...

    async def find_by_project(self, project_id: str) -> list[AppDatabase]: ...

    async def delete(self, database_id: str, project_id: str) -> None: ...


class AppCollectionRepository(Protocol):
    async def create(
        self,
        project_id: str,
        database_id: str,
        name: str,
        fields: list[dict[str, object]],
        record_identifier: str | None = None,
    ) -> AppCollection: ...

    async def find_by_id_and_project(
        self, collection_id: str, project_id: str
    ) -> AppCollection | None: ...

    async def find_by_project(
        self, project_id: str, database_id: str | None = None
    ) -> list[AppCollection]: ...

    async def update(
        self, collection_id: str, project_id: str, **fields: object
    ) -> AppCollection: ...

    async def delete(self, collection_id: str, project_id: str) -> None: ...


class AppRecordRepository(Protocol):
    async def create(
        self, project_id: str, collection_id: str, environment: str, values: dict[str, object]
    ) -> AppRecord: ...

    async def find_by_id_and_scope(
        self, record_id: str, project_id: str, collection_id: str, environment: str
    ) -> AppRecord | None: ...

    async def find_by_scope(
        self, project_id: str, collection_id: str, environment: str
    ) -> list[AppRecord]: ...

    async def update(
        self,
        record_id: str,
        project_id: str,
        collection_id: str,
        environment: str,
        values: dict[str, object],
    ) -> AppRecord: ...

    async def delete(
        self, record_id: str, project_id: str, collection_id: str, environment: str
    ) -> None: ...
