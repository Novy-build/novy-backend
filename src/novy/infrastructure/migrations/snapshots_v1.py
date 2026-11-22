import asyncio
import logging
import sys
from typing import Any

from pymongo.asynchronous.collection import AsyncCollection

from novy.infrastructure.mongodb import create_mongo_client, database_name_from_connection_string
from novy.infrastructure.settings import Settings
from novy.infrastructure.snapshots import SNAPSHOTS_COLLECTION


async def ensure_snapshot_indexes(collection: AsyncCollection[dict[str, Any]]) -> None:
    await collection.create_index(
        [("project_id", 1), ("environment", 1), ("_id", 1)],
        name="snapshots_project_env_v1",
    )


async def _provision() -> None:
    settings = Settings()
    if settings.mongodb_connection_string is None:
        raise RuntimeError("MongoDB connection is not configured")
    client = create_mongo_client(settings.mongodb_connection_string)
    try:
        db_name = database_name_from_connection_string(settings.mongodb_connection_string)
        collection = client[db_name][SNAPSHOTS_COLLECTION]
        await ensure_snapshot_indexes(collection)
    finally:
        await client.close()


def main() -> int:
    previous_logging_level = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        asyncio.run(_provision())
    except Exception:
        print(
            "Snapshot index setup failed; check database access and index compatibility.",
            file=sys.stderr,
        )
        return 1
    finally:
        logging.disable(previous_logging_level)
    print("Snapshot index is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
