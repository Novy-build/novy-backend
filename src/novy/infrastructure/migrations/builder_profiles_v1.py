import asyncio
import logging
import sys
from typing import Any

from pymongo import ASCENDING
from pymongo.asynchronous.collection import AsyncCollection

from novy.infrastructure.builder_profiles import BUILDER_IDENTITY_INDEX, BUILDER_PROFILES_COLLECTION
from novy.infrastructure.mongodb import create_mongo_client, database_name_from_connection_string
from novy.infrastructure.settings import Settings


async def ensure_builder_profile_indexes(collection: AsyncCollection[dict[str, Any]]) -> None:
    await collection.create_index(
        [("issuer", ASCENDING), ("subject", ASCENDING)],
        unique=True,
        name=BUILDER_IDENTITY_INDEX,
        collation={"locale": "simple"},
    )


async def _provision() -> None:
    settings = Settings()
    if settings.mongodb_connection_string is None:
        raise RuntimeError("MongoDB connection is not configured")
    client = create_mongo_client(settings.mongodb_connection_string)
    try:
        db_name = database_name_from_connection_string(settings.mongodb_connection_string)
        collection = client[db_name][BUILDER_PROFILES_COLLECTION]
        await ensure_builder_profile_indexes(collection)
    finally:
        await client.close()


def main() -> int:
    previous_logging_level = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        asyncio.run(_provision())
    except Exception:
        print(
            "Builder profile index setup failed; check database access and index compatibility.",
            file=sys.stderr,
        )
        return 1
    finally:
        logging.disable(previous_logging_level)
    print("Builder profile index is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
