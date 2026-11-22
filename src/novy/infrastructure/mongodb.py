from typing import Any
from urllib.parse import urlsplit

from pydantic import SecretStr
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

PLATFORM_DATABASE_NAME = "platform_db"


def database_name_from_connection_string(connection_string: SecretStr) -> str:
    """Extract the database name from a MongoDB connection string.

    Falls back to ``PLATFORM_DATABASE_NAME`` when no database is specified
    in the URI path.
    """
    try:
        parts = urlsplit(connection_string.get_secret_value())
        path = parts.path.strip("/")
        if path:
            # The path may contain additional segments; use the first one.
            return path.split("/")[0]
    except (ValueError, AttributeError):
        pass
    return PLATFORM_DATABASE_NAME


def create_mongo_client(connection_string: SecretStr) -> AsyncMongoClient[dict[str, Any]]:
    try:
        return AsyncMongoClient(
            connection_string.get_secret_value(),
            tls=True,
            appname="novy-backend",
            maxPoolSize=10,
            connectTimeoutMS=2000,
            serverSelectionTimeoutMS=3000,
            waitQueueTimeoutMS=3000,
            timeoutMS=5000,
        )
    except (PyMongoError, ValueError, TypeError):
        raise RuntimeError("Invalid MongoDB connection configuration") from None
