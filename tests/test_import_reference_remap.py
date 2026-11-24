"""Tests that project import rewrites references to the fresh collection IDs."""

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from pymongo.asynchronous.database import AsyncDatabase

from novy.application.import_project import ImportPayload
from novy.infrastructure.import_project import MongoImportRepository


def make_fake_database() -> tuple[MagicMock, dict[str, MagicMock]]:
    session = MagicMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=None)
    session.start_transaction = AsyncMock(return_value=transaction)
    session.commit_transaction = AsyncMock()
    session.abort_transaction = AsyncMock()
    session.end_session = AsyncMock()
    client = MagicMock()
    client.start_session = MagicMock(return_value=session)
    db = MagicMock()
    db.client = client
    collections: dict[str, MagicMock] = {}

    def get_collection(name: str) -> MagicMock:
        collection = MagicMock()
        collection.insert_one = AsyncMock()
        collection.find_one = AsyncMock(return_value=None)
        collections[name] = collection
        return collection

    db.__getitem__.side_effect = get_collection
    return db, collections


def test_import_remaps_project_draft_references() -> None:
    async def run() -> None:
        db, collections = make_fake_database()
        payload = ImportPayload(
            project={
                "name": "Imported",
                "schemaVersion": 14,
                "screens": [
                    {
                        "routeParameters": [
                            {
                                "source": {
                                    "kind": "record-id",
                                    "collectionId": "old_coll",
                                    "identifierKey": "noteId",
                                },
                            }
                        ],
                        "logicGraphs": [
                            {
                                "nodes": [
                                    {
                                        "payload": {
                                            "type": "load-list-items",
                                            "collectionId": "old_coll",
                                            "databaseId": "old_db",
                                            "fields": [{"id": "note_title"}],
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
            app_data={
                "databases": [{"id": "old_db", "name": "Database"}],
                "collections": [
                    {
                        "id": "old_coll",
                        "databaseId": "old_db",
                        "name": "Notes",
                        "fields": [{"id": "note_title", "key": "title", "type": "text"}],
                    }
                ],
                "records": [],
            },
        )

        await MongoImportRepository(cast(AsyncDatabase[Any], db)).import_project(
            "owner", payload, "key-1"
        )

        project_doc = collections["projects"].insert_one.call_args.args[0]
        collection_doc = collections["app_collections"].insert_one.call_args.args[0]
        new_id = str(collection_doc["_id"])
        screen = project_doc["draft"]["screens"][0]
        payload_node = screen["logicGraphs"][0]["nodes"][0]["payload"]

        assert screen["routeParameters"][0]["source"]["collectionId"] == new_id
        assert payload_node["collectionId"] == new_id
        assert payload_node["databaseId"] == str(
            collections["app_databases"].insert_one.call_args.args[0]["_id"]
        )
        assert payload_node["fields"] == [{"id": "note_title"}]

    asyncio.run(run())
