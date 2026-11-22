"""Explicit, operator-invoked builder session index provisioning."""

import asyncio
import logging
import sys
from typing import Any

from pymongo import ASCENDING
from pymongo.asynchronous.collection import AsyncCollection

from novy.infrastructure.builder_sessions import (
    BUILDER_SESSIONS_COLLECTION,
    DIGEST_INDEX,
    TTL_INDEX,
)
from novy.infrastructure.mongodb import create_mongo_client, database_name_from_connection_string
from novy.infrastructure.settings import Settings


async def ensure_builder_session_indexes(collection: AsyncCollection[dict[str, Any]]) -> None:
    await collection.create_index([("digest", ASCENDING)], unique=True, name=DIGEST_INDEX)
    await collection.create_index(
        [("absolute_expires_at", ASCENDING)], expireAfterSeconds=0, name=TTL_INDEX
    )


async def _provision() -> None:
    settings = Settings()
    if settings.mongodb_connection_string is None:
        raise RuntimeError("MongoDB connection is not configured")
    client = create_mongo_client(settings.mongodb_connection_string)
    try:
        db_name = database_name_from_connection_string(settings.mongodb_connection_string)
        collection = client[db_name][BUILDER_SESSIONS_COLLECTION]
        await ensure_builder_session_indexes(collection)
    finally:
        await client.close()


def main() -> int:
    previous_logging_level = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        asyncio.run(_provision())
    except Exception:
        print(
            "Builder session index setup failed; check database access and index compatibility.",
            file=sys.stderr,
        )
        return 1
    finally:
        logging.disable(previous_logging_level)
    print("Builder session indexes are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
