"""Explicit index provisioning for project-scoped application data."""

import asyncio
import logging
import sys
from typing import Any

from pymongo import ASCENDING
from pymongo.asynchronous.collection import AsyncCollection

from novy.infrastructure.app_data import (
    APP_COLLECTIONS_COLLECTION,
    APP_DATABASES_COLLECTION,
    APP_RECORDS_COLLECTION,
)
from novy.infrastructure.mongodb import create_mongo_client, database_name_from_connection_string
from novy.infrastructure.settings import Settings


async def ensure_app_data_indexes(database: Any) -> None:
    databases: AsyncCollection[dict[str, Any]] = database[APP_DATABASES_COLLECTION]
    collections: AsyncCollection[dict[str, Any]] = database[APP_COLLECTIONS_COLLECTION]
    records: AsyncCollection[dict[str, Any]] = database[APP_RECORDS_COLLECTION]
    await databases.create_index([("project_id", ASCENDING)], name="project_id_v1")
    await collections.create_index([("project_id", ASCENDING)], name="project_id_v1")
    await records.create_index(
        [("project_id", ASCENDING), ("collection_id", ASCENDING), ("environment", ASCENDING)],
        name="scope_v1",
    )


async def _provision() -> None:
    settings = Settings()
    if settings.mongodb_connection_string is None:
        raise RuntimeError("MongoDB connection is not configured")
    client = create_mongo_client(settings.mongodb_connection_string)
    try:
        db_name = database_name_from_connection_string(settings.mongodb_connection_string)
        await ensure_app_data_indexes(client[db_name])
    finally:
        await client.close()


def main() -> int:
    previous_logging_level = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        asyncio.run(_provision())
    except Exception:
        print(
            "Application data index setup failed; check database access and index compatibility.",
            file=sys.stderr,
        )
        return 1
    finally:
        logging.disable(previous_logging_level)
    print("Application data indexes are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
