from typing import Any

from pydantic import SecretStr
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

PLATFORM_DATABASE_NAME = "platform_db"


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
