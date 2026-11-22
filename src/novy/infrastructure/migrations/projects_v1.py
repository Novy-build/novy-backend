import asyncio
import logging
import sys
from typing import Any

from pymongo import ASCENDING
from pymongo.asynchronous.collection import AsyncCollection

from novy.infrastructure.mongodb import PLATFORM_DATABASE_NAME, create_mongo_client
from novy.infrastructure.projects import PROJECTS_COLLECTION, PROJECTS_OWNER_UPDATED_INDEX
from novy.infrastructure.settings import Settings


async def ensure_project_indexes(collection: AsyncCollection[dict[str, Any]]) -> None:
    await collection.create_index(
        [("owner_id", ASCENDING), ("updated_at", ASCENDING), ("_id", ASCENDING)],
        name=PROJECTS_OWNER_UPDATED_INDEX,
    )


async def _provision() -> None:
    settings = Settings()
    if settings.mongodb_connection_string is None:
        raise RuntimeError("MongoDB connection is not configured")
    client = create_mongo_client(settings.mongodb_connection_string)
    try:
        collection = client[PLATFORM_DATABASE_NAME][PROJECTS_COLLECTION]
        await ensure_project_indexes(collection)
    finally:
        await client.close()


def main() -> int:
    previous_logging_level = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        asyncio.run(_provision())
    except Exception:
        print(
            "Project index setup failed; check database access and index compatibility.",
            file=sys.stderr,
        )
        return 1
    finally:
        logging.disable(previous_logging_level)
    print("Project index is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
