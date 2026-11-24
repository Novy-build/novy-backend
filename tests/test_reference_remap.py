"""Tests for typed draft reference remapping."""

from typing import Any

from novy.infrastructure.duplicate import _remap_draft
from novy.infrastructure.reference_remap import remap_draft_references


def test_remaps_collection_and_database_references_recursively() -> None:
    draft: dict[str, Any] = {
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
                "nodes": [
                    {
                        "props": {"dataSource": {"kind": "collection", "collectionId": "old_coll"}},
                        "bindings": [{"value": {"source": "list-item", "fieldId": "note_title"}}],
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
    }

    remapped = remap_draft_references(
        draft,
        {"old_db": "new_db"},
        {"old_coll": "new_coll"},
    )

    screen = remapped["screens"][0]
    assert screen["routeParameters"][0]["source"]["collectionId"] == "new_coll"
    assert screen["nodes"][0]["props"]["dataSource"]["collectionId"] == "new_coll"
    payload = screen["logicGraphs"][0]["nodes"][0]["payload"]
    assert payload["collectionId"] == "new_coll"
    assert payload["databaseId"] == "new_db"
    assert payload["fields"] == [{"id": "note_title"}]
    assert screen["nodes"][0]["bindings"][0]["value"]["fieldId"] == "note_title"
    assert draft["screens"][0]["nodes"][0]["props"]["dataSource"]["collectionId"] == "old_coll"


def test_preserves_unknown_strings_and_dynamic_expressions() -> None:
    draft: dict[str, Any] = {
        "inputMappings": {"$recordId": {"source": "list-item", "fieldId": "$id"}},
        "note": "collectionId is just text here",
        "payload": {"recordId": "old_coll", "collection_id": "unmapped_coll"},
    }

    remapped = remap_draft_references(draft, {}, {"old_coll": "new_coll"})

    assert remapped["inputMappings"]["$recordId"]["fieldId"] == "$id"
    assert remapped["note"] == "collectionId is just text here"
    assert remapped["payload"]["recordId"] == "old_coll"
    # A known reference key with an unmapped value is left untouched.
    assert remapped["payload"]["collection_id"] == "unmapped_coll"


def test_duplicate_helper_delegates_to_shared_remapper() -> None:
    draft: dict[str, Any] = {"payload": {"collectionId": "old_coll"}}

    assert _remap_draft(draft, {}, {"old_coll": "new_coll"}) == {
        "payload": {"collectionId": "new_coll"}
    }
    assert draft["payload"]["collectionId"] == "old_coll"
