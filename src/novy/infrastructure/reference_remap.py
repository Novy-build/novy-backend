"""Typed reference remapping for drafts that gain fresh persisted IDs.

Used when duplicating, importing, or provisioning a project: collections and
databases receive new ObjectIds, so every typed reference in the project draft
must be rewritten to keep logic, route parameters, and bindings resolvable.
Arbitrary strings (including dynamic expressions such as ``$recordId``) and
unknown fields are preserved, and field IDs are kept as authored.
"""

from __future__ import annotations

import copy
from typing import Any

_COLLECTION_REFERENCE_KEYS = ("collectionId", "collection_id", "listSourceCollectionId")
_DATABASE_REFERENCE_KEYS = ("databaseId", "database_id")


def remap_draft_references(
    draft: dict[str, Any],
    db_id_map: dict[str, str],
    coll_id_map: dict[str, str],
) -> dict[str, Any]:
    """Return a deep copy of ``draft`` with known persisted references remapped."""

    def _remap_value(value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, val in value.items():
                if key in _COLLECTION_REFERENCE_KEYS and isinstance(val, str):
                    result[key] = coll_id_map.get(val, val)
                elif key in _DATABASE_REFERENCE_KEYS and isinstance(val, str):
                    result[key] = db_id_map.get(val, val)
                else:
                    result[key] = _remap_value(val)
            return result
        if isinstance(value, list):
            return [_remap_value(item) for item in value]
        return value

    remapped = _remap_value(copy.deepcopy(draft))
    return remapped if isinstance(remapped, dict) else draft
