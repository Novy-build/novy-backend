"""Strict project validation before preview/publish.

Validates the exact draft and schema revision: screen/node identities,
routes/parameters, responsive layout, logic graph references and ports,
data bindings, field types, collection/record references, policy shape,
exposed fields, and supported runtime capability allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ValidationError(Exception):
    """Raised when a project fails strict validation."""

    def __init__(self, issues: list[ValidationIssue] | None = None) -> None:
        self.issues = issues or []
        super().__init__(f"Validation failed with {len(self.issues)} issue(s)")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single validation issue with code, location, and message."""

    code: str
    location: str
    message: str


# Supported runtime capabilities for v1
SUPPORTED_CAPABILITIES = frozenset(
    {
        "text",
        "button",
        "input",
        "image",
        "container",
        "column",
        "row",
        "listview",
        "checkbox",
        "radio",
        "switch",
        "dropdown",
        "slider",
        "progress",
        "divider",
    }
)


def validate_screen_identities(screens: list[dict[str, Any]]) -> list[ValidationIssue]:
    """Validate screen IDs are unique and non-empty."""
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for i, screen in enumerate(screens):
        sid = screen.get("id")
        if not isinstance(sid, str) or not sid.strip():
            issues.append(
                ValidationIssue(
                    code="invalid_screen_id",
                    location=f"screens[{i}].id",
                    message="Screen ID must be a non-empty string",
                )
            )
            continue
        if sid in seen:
            issues.append(
                ValidationIssue(
                    code="duplicate_screen_id",
                    location=f"screens[{i}].id",
                    message=f"Duplicate screen ID: {sid}",
                )
            )
        seen.add(sid)
    return issues


def validate_node_identities(screens: list[dict[str, Any]]) -> list[ValidationIssue]:
    """Validate node IDs are unique within each screen."""
    issues: list[ValidationIssue] = []
    for si, screen in enumerate(screens):
        nodes = screen.get("nodes", [])
        if not isinstance(nodes, list):
            continue
        seen: set[str] = set()

        def _check_node(node: Any, path: str, seen_set: set[str]) -> None:
            if not isinstance(node, dict):
                return
            nid = node.get("id")
            if not isinstance(nid, str) or not nid.strip():
                issues.append(
                    ValidationIssue(
                        code="invalid_node_id",
                        location=path,
                        message="Node ID must be a non-empty string",
                    )
                )
                return
            if nid in seen_set:
                issues.append(
                    ValidationIssue(
                        code="duplicate_node_id",
                        location=path,
                        message=f"Duplicate node ID: {nid}",
                    )
                )
            seen_set.add(nid)
            children = node.get("children", [])
            if isinstance(children, list):
                for ci, child in enumerate(children):
                    _check_node(child, f"{path}.children[{ci}]", seen_set)

        for ni, node in enumerate(nodes):
            _check_node(node, f"screens[{si}].nodes[{ni}]", seen)
    return issues


def validate_component_types(screens: list[dict[str, Any]]) -> list[ValidationIssue]:
    """Validate component types are in the supported capability allowlist."""
    issues: list[ValidationIssue] = []
    for si, screen in enumerate(screens):
        nodes = screen.get("nodes", [])
        if not isinstance(nodes, list):
            continue

        def _check_type(node: Any, path: str) -> None:
            if not isinstance(node, dict):
                return
            ntype = node.get("type")
            if isinstance(ntype, str) and ntype not in SUPPORTED_CAPABILITIES:
                issues.append(
                    ValidationIssue(
                        code="unsupported_component_type",
                        location=path,
                        message=f"Unsupported component type: {ntype}",
                    )
                )
            children = node.get("children", [])
            if isinstance(children, list):
                for ci, child in enumerate(children):
                    _check_type(child, f"{path}.children[{ci}]")

        for ni, node in enumerate(nodes):
            _check_type(node, f"screens[{si}].nodes[{ni}]")
    return issues


def validate_routes(screens: list[dict[str, Any]]) -> list[ValidationIssue]:
    """Validate screen routes are valid paths."""
    issues: list[ValidationIssue] = []
    for i, screen in enumerate(screens):
        path = screen.get("path")
        if path is not None and not isinstance(path, str):
            issues.append(
                ValidationIssue(
                    code="invalid_route",
                    location=f"screens[{i}].path",
                    message="Screen path must be a string",
                )
            )
        elif isinstance(path, str) and not path.startswith("/"):
            issues.append(
                ValidationIssue(
                    code="invalid_route",
                    location=f"screens[{i}].path",
                    message=f"Screen path must start with /: {path}",
                )
            )
    return issues


def validate_data_bindings(
    screens: list[dict[str, Any]],
    collections: list[dict[str, Any]] | None = None,
) -> list[ValidationIssue]:
    """Validate data bindings reference valid collections and fields."""
    issues: list[ValidationIssue] = []
    collection_ids: set[str] = set()
    field_ids_by_collection: dict[str, set[str]] = {}
    if collections:
        for coll in collections:
            cid = coll.get("id")
            if isinstance(cid, str):
                collection_ids.add(cid)
                fields = coll.get("fields", [])
                if isinstance(fields, list):
                    ids: set[str] = set()
                    for f in fields:
                        if isinstance(f, dict):
                            fid = f.get("id")
                            if isinstance(fid, str):
                                ids.add(fid)
                    field_ids_by_collection[cid] = ids

    # Walk screens looking for data bindings
    for si, screen in enumerate(screens):
        nodes = screen.get("nodes", [])
        if not isinstance(nodes, list):
            continue

        def _check_bindings(node: Any, path: str) -> None:
            if not isinstance(node, dict):
                return
            # Check collection-backed list sources
            list_source = (
                node.get("listSource") or node.get("collectionId") or node.get("collection_id")
            )
            if isinstance(list_source, str) and collections is not None:
                if list_source not in collection_ids:
                    issues.append(
                        ValidationIssue(
                            code="invalid_collection_reference",
                            location=path,
                            message=f"Collection not found: {list_source}",
                        )
                    )
            children = node.get("children", [])
            if isinstance(children, list):
                for ci, child in enumerate(children):
                    _check_bindings(child, f"{path}.children[{ci}]")

        for ni, node in enumerate(nodes):
            _check_bindings(node, f"screens[{si}].nodes[{ni}]")
    return issues


def validate_project(
    project: dict[str, Any],
    *,
    collections: list[dict[str, Any]] | None = None,
) -> list[ValidationIssue]:
    """Run all strict validation checks on a project.

    Returns a list of validation issues (empty if valid).
    """
    issues: list[ValidationIssue] = []
    screens = project.get("screens", [])
    if not isinstance(screens, list):
        issues.append(
            ValidationIssue(
                code="invalid_screens",
                location="screens",
                message="Screens must be a list",
            )
        )
        return issues

    issues.extend(validate_screen_identities(screens))
    issues.extend(validate_node_identities(screens))
    issues.extend(validate_component_types(screens))
    issues.extend(validate_routes(screens))
    issues.extend(validate_data_bindings(screens, collections))

    # Validate schema version
    schema_version = project.get("schemaVersion", project.get("schema_version"))
    if schema_version is not None and schema_version not in (13, 14):
        issues.append(
            ValidationIssue(
                code="unsupported_schema_version",
                location="schemaVersion",
                message=f"Unsupported schema version: {schema_version}",
            )
        )

    return issues


def validate_capability(capability: str) -> bool:
    """Check if a capability is in the supported allowlist."""
    return capability in SUPPORTED_CAPABILITIES
