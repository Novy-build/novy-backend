"""Tests for atomic template provisioning and reference remapping."""

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from novy.application.template_provisioning import (
    TemplateBlueprint,
    TemplateCollection,
    TemplateDatabase,
    TemplateProvisioningError,
    TemplateProvisioningService,
)
from novy.infrastructure.template_provisioning import MongoTemplateProvisioningStore

REFERENCE = "template-notes-collection"


def make_fake_database() -> tuple[MagicMock, dict[str, MagicMock], MagicMock]:
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
        collections[name] = collection
        return collection

    db.__getitem__.side_effect = get_collection
    return db, collections, session


def make_blueprint(reference_id: str = REFERENCE) -> TemplateBlueprint:
    return TemplateBlueprint(
        name="Notes",
        project_type="app",
        screens=[
            {
                "id": "screen_home",
                "behaviors": [],
                "nodes": [
                    {
                        "id": "list",
                        "props": {"dataSource": {"kind": "static", "fields": []}},
                        "bindings": [{"value": {"source": "list-item", "fieldId": "note_title"}}],
                    }
                ],
                "logicGraphs": [
                    {
                        "nodes": [
                            {
                                "payload": {
                                    "type": "load-list-items",
                                    "targetNodeId": "list",
                                    "collectionId": reference_id,
                                    "fields": [{"id": "note_title"}],
                                },
                            }
                        ],
                    }
                ],
            }
        ],
        databases=[
            TemplateDatabase(
                name="Notes database",
                collections=[
                    TemplateCollection(
                        name="Notes",
                        reference_id=reference_id,
                        fields=[
                            {"id": "note_title", "key": "title", "type": "text", "required": True}
                        ],
                        records=[{"values": {"title": "First note"}}],
                        identifier={"key": "noteId", "type": "string", "generation": "backend"},
                    )
                ],
            )
        ],
    )


def test_provisioning_remaps_aliases_and_preserves_field_ids() -> None:
    async def run() -> None:
        db, collections, session = make_fake_database()
        store = MongoTemplateProvisioningStore(cast(AsyncDatabase[Any], db))
        service = TemplateProvisioningService(store)

        await service.provision("owner", make_blueprint())

        project_doc = collections["projects"].insert_one.call_args.args[0]
        collection_doc = collections["app_collections"].insert_one.call_args.args[0]
        record_doc = collections["app_records"].insert_one.call_args.args[0]
        new_id = str(collection_doc["_id"])

        payload = project_doc["draft"]["screens"][0]["logicGraphs"][0]["nodes"][0]["payload"]
        assert payload["collectionId"] == new_id
        assert payload["collectionId"] != REFERENCE
        assert payload["fields"] == [{"id": "note_title"}]
        assert collection_doc["record_identifier"] == "noteId"
        assert collection_doc["fields"][0]["id"] == "note_title"
        assert record_doc["collection_id"] == new_id
        session.commit_transaction.assert_awaited_once()

    asyncio.run(run())


def test_provisioning_requires_unique_reference_ids() -> None:
    service = TemplateProvisioningService(MagicMock())

    missing = make_blueprint(reference_id="")
    with pytest.raises(TemplateProvisioningError, match="reference id is required"):
        service.validate_blueprint(missing)

    duplicate = make_blueprint()
    duplicate.databases[0].collections.append(duplicate.databases[0].collections[0])
    with pytest.raises(TemplateProvisioningError, match="Duplicate collection reference"):
        service.validate_blueprint(duplicate)
