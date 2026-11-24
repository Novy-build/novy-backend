"""Strict validation of the v2 generated-app manifest.

The manifest is client-supplied, so every section is checked before it reaches
the generator: envelope shape, privacy flags, project/layout consistency,
component and logic references, collection bindings, capability policy and
output-path safety. Existing strict project validators are reused where they
apply; export-specific checks live here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from novy.domain.generation import (
    DEFERRED_ACTION_TYPES,
    DEFERRED_CAPABILITIES,
    SUPPORTED_ACTION_TYPES,
    SUPPORTED_COMPONENT_TYPES,
    SUPPORTED_RUNTIME_CAPABILITIES,
    InvalidManifestError,
    UnsupportedCapabilityError,
    ValidationIssue,
)
from novy.domain.validation import (
    validate_node_identities,
    validate_routes,
    validate_screen_identities,
)

MAX_MANIFEST_STRING = 20_000
MAX_SCREENS = 100
MAX_NODES = 2000
MAX_DEPTH = 24

_ENVELOPE_KEYS = frozenset(
    {"manifestVersion", "generatedAt", "project", "layout", "appData", "runtime", "security"}
)
_BANNED_KEYS = frozenset(
    {
        "secret",
        "secrets",
        "password",
        "passwords",
        "token",
        "tokens",
        "apikey",
        "apikeys",
        "credential",
        "credentials",
        "privatekey",
        "accesstoken",
        "refreshtoken",
    }
)
_BANNED_VALUES = ("data:image/", "-----begin ", "biometric_template")
_EVENTS = frozenset({"click", "change", "load"})
_PORTS = frozenset(
    {
        "in",
        "next",
        "true",
        "false",
        "success",
        "error",
        "documents",
        "text-splitter",
        "embeddings",
        "chat-model",
        "memory",
        "tool",
        "reranker",
        "prompt-template",
    }
)


def _as_object(
    value: object, location: str, issues: list[ValidationIssue]
) -> dict[str, object] | None:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return cast(dict[str, object], value)
    issues.append(ValidationIssue("invalid_type", location, "Expected an object"))
    return None


def _as_list(value: object, location: str, issues: list[ValidationIssue]) -> list[object] | None:
    if isinstance(value, list):
        return cast(list[object], value)
    issues.append(ValidationIssue("invalid_type", location, "Expected a list"))
    return None


def _as_text(value: object, location: str, issues: list[ValidationIssue]) -> str | None:
    if isinstance(value, str) and value.strip() and len(value) <= MAX_MANIFEST_STRING:
        return value
    issues.append(ValidationIssue("invalid_value", location, "Expected a non-empty string"))
    return None


def _as_bool(value: object, location: str, issues: list[ValidationIssue]) -> bool | None:
    if isinstance(value, bool):
        return value
    issues.append(ValidationIssue("invalid_type", location, "Expected a boolean"))
    return None


def _as_int(value: object, location: str, issues: list[ValidationIssue]) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    issues.append(ValidationIssue("invalid_type", location, "Expected an integer"))
    return None


def _from_domain(issues: list[ValidationIssue], others: list[Any]) -> None:
    """Map shared validator issues into this module's issue type."""
    for issue in others:
        issues.append(
            ValidationIssue(
                code=str(issue.code), location=str(issue.location), message=str(issue.message)
            )
        )


def _walk_nodes(nodes: list[object], location: str, visit: Any) -> None:
    """Visit each node, including nested item-frame children."""
    for index, raw in enumerate(nodes):
        node_location = f"{location}[{index}]"
        if not isinstance(raw, dict):
            continue
        node = cast(dict[str, object], raw)
        visit(node, node_location)
        children = node.get("children")
        if isinstance(children, list):
            _walk_nodes(cast(list[object], children), f"{node_location}.children", visit)
        item_frame = node.get("itemFrame")
        if isinstance(item_frame, dict):
            frame = cast(dict[str, object], item_frame)
            visit(frame, f"{node_location}.itemFrame")
            frame_children = frame.get("children")
            if isinstance(frame_children, list):
                _walk_nodes(
                    cast(list[object], frame_children),
                    f"{node_location}.itemFrame.children",
                    visit,
                )


def _scan_for_sensitive_content(
    value: object, location: str, depth: int, issues: list[ValidationIssue]
) -> None:
    if depth > MAX_DEPTH:
        issues.append(
            ValidationIssue("manifest_too_deep", location, f"Manifest nesting exceeds {MAX_DEPTH}")
        )
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(fragment in lowered for fragment in _BANNED_VALUES):
            issues.append(
                ValidationIssue("sensitive_value", location, "Sensitive content is not exportable")
            )
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if normalized in _BANNED_KEYS:
                issues.append(
                    ValidationIssue(
                        "sensitive_key", f"{location}.{key}", "Sensitive key is not exportable"
                    )
                )
            _scan_for_sensitive_content(nested, f"{location}.{key}", depth + 1, issues)
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _scan_for_sensitive_content(nested, f"{location}[{index}]", depth + 1, issues)


def _validate_collections(
    app_data: dict[str, object], issues: list[ValidationIssue]
) -> tuple[set[str], dict[str, set[str]], dict[str, str]]:
    collections = _as_list(app_data.get("collections"), "appData.collections", issues)
    ids: set[str] = set()
    fields: dict[str, set[str]] = {}
    identifiers: dict[str, str] = {}
    if collections is None:
        return ids, fields, identifiers
    for index, raw in enumerate(collections):
        location = f"appData.collections[{index}]"
        collection = _as_object(raw, location, issues)
        if collection is None:
            continue
        collection_id = _as_text(collection.get("id"), f"{location}.id", issues)
        if collection_id is None:
            continue
        ids.add(collection_id)
        field_ids: set[str] = set()
        raw_fields = _as_list(collection.get("fields"), f"{location}.fields", issues)
        if raw_fields is not None:
            for field_index, field_raw in enumerate(raw_fields):
                field = _as_object(field_raw, f"{location}.fields[{field_index}]", issues)
                if field is None:
                    continue
                field_id = _as_text(field.get("id"), f"{location}.fields[{field_index}].id", issues)
                if field_id is not None:
                    field_ids.add(field_id)
        fields[collection_id] = field_ids
        identifier = collection.get("identifier")
        if isinstance(identifier, dict):
            key = identifier.get("key")
            if isinstance(key, str):
                identifiers[collection_id] = key
    return ids, fields, identifiers


def _validate_project(
    project: dict[str, object],
    collection_ids: set[str],
    field_ids: dict[str, set[str]],
    identifiers: dict[str, str],
    issues: list[ValidationIssue],
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    schema_version = project.get("schemaVersion")
    if schema_version is not None and (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in (13, 14)
    ):
        issues.append(
            ValidationIssue(
                "unsupported_schema_version", "project.schemaVersion", "Unsupported schema version"
            )
        )
    for key in ("id", "name", "createdAt", "updatedAt"):
        _as_text(project.get(key), f"project.{key}", issues)
    project_type = project.get("projectType")
    if project_type is not None and project_type not in ("app", "website"):
        issues.append(
            ValidationIssue("invalid_project_type", "project.projectType", "Invalid project type")
        )

    screens = _as_list(project.get("screens"), "project.screens", issues)
    if screens is None:
        return {}, {}, []
    deferred_actions: list[str] = []
    if len(screens) > MAX_SCREENS:
        issues.append(
            ValidationIssue(
                "too_many_screens",
                "project.screens",
                f"At most {MAX_SCREENS} screens are supported",
            )
        )

    typed_screens = [screen for screen in screens if isinstance(screen, dict)]
    _from_domain(issues, validate_screen_identities(typed_screens))
    _from_domain(issues, validate_node_identities(typed_screens))
    _from_domain(issues, validate_routes(typed_screens))

    screen_paths: dict[str, str] = {}
    screen_ids: set[str] = set()
    node_count = 0
    for screen_index, raw_screen in enumerate(screens):
        location = f"project.screens[{screen_index}]"
        screen = _as_object(raw_screen, location, issues)
        if screen is None:
            continue
        screen_id = _as_text(screen.get("id"), f"{location}.id", issues)
        if screen_id is None:
            continue
        screen_ids.add(screen_id)
        path_value = screen.get("path")
        if isinstance(path_value, str):
            screen_paths[screen_id] = path_value

        nodes = _as_list(screen.get("nodes"), f"{location}.nodes", issues)
        if nodes is None:
            continue

        def visit_node(node: dict[str, object], node_location: str) -> None:
            nonlocal node_count
            node_count += 1
            node_id = _as_text(node.get("id"), f"{node_location}.id", issues)
            node_type = _as_text(
                node.get("type") or node.get("kind"), f"{node_location}.type", issues
            )
            if node_type is not None and node_type not in SUPPORTED_COMPONENT_TYPES:
                issues.append(
                    ValidationIssue(
                        "unsupported_component_type",
                        f"{node_location}.type",
                        f"Unsupported component type: {node_type}",
                    )
                )
            _validate_node_references(
                node, node_location, node_id, collection_ids, field_ids, identifiers, issues
            )

        _walk_nodes(nodes, f"{location}.nodes", visit_node)

        graphs = _as_list(screen.get("logicGraphs"), f"{location}.logicGraphs", issues)
        graph_ids: set[str] = set()
        graph_node_ids: dict[str, set[str]] = {}
        if graphs is not None:
            for graph_index, raw_graph in enumerate(graphs):
                graph_location = f"{location}.logicGraphs[{graph_index}]"
                graph = _as_object(raw_graph, graph_location, issues)
                if graph is None:
                    continue
                graph_id = _as_text(graph.get("id"), f"{graph_location}.id", issues)
                if graph_id is not None:
                    graph_ids.add(graph_id)
                graph_nodes = _as_list(graph.get("nodes"), f"{graph_location}.nodes", issues)
                ids_in_graph: set[str] = set()
                if graph_nodes is not None:
                    for node_index, raw_node in enumerate(graph_nodes):
                        node_location = f"{graph_location}.nodes[{node_index}]"
                        logic_node = _as_object(raw_node, node_location, issues)
                        if logic_node is None:
                            continue
                        node_id = _as_text(logic_node.get("id"), f"{node_location}.id", issues)
                        if node_id is not None:
                            ids_in_graph.add(node_id)
                        deferred_actions.extend(
                            _validate_logic_node(
                                logic_node, node_location, collection_ids, field_ids, issues
                            )
                        )
                if graph_id is not None:
                    graph_node_ids[graph_id] = ids_in_graph
                source = graph.get("sourceNodeId")
                if isinstance(source, str) and source not in ids_in_graph:
                    issues.append(
                        ValidationIssue(
                            "invalid_graph_source",
                            f"{graph_location}.sourceNodeId",
                            "Graph source node does not exist",
                        )
                    )
                edges = _as_list(graph.get("edges"), f"{graph_location}.edges", issues)
                if edges is not None:
                    for edge_index, raw_edge in enumerate(edges):
                        edge_location = f"{graph_location}.edges[{edge_index}]"
                        edge = _as_object(raw_edge, edge_location, issues)
                        if edge is None:
                            continue
                        for endpoint in ("fromNodeId", "toNodeId"):
                            target = edge.get(endpoint)
                            if isinstance(target, str) and target not in ids_in_graph:
                                issues.append(
                                    ValidationIssue(
                                        "invalid_edge_reference",
                                        f"{edge_location}.{endpoint}",
                                        "Edge references a node that does not exist",
                                    )
                                )
                        for port_key in ("fromPort", "toPort"):
                            port = edge.get(port_key)
                            if port is not None and port not in _PORTS:
                                issues.append(
                                    ValidationIssue(
                                        "invalid_edge_port",
                                        f"{edge_location}.{port_key}",
                                        "Edge port is not recognized",
                                    )
                                )

        behaviors = _as_list(screen.get("behaviors"), f"{location}.behaviors", issues)
        if behaviors is not None:
            for behavior_index, raw_behavior in enumerate(behaviors):
                behavior_location = f"{location}.behaviors[{behavior_index}]"
                behavior = _as_object(raw_behavior, behavior_location, issues)
                if behavior is None:
                    continue
                target = _as_text(
                    behavior.get("targetNodeId"), f"{behavior_location}.targetNodeId", issues
                )
                graph_id = _as_text(behavior.get("graphId"), f"{behavior_location}.graphId", issues)
                event = behavior.get("event")
                if event not in _EVENTS:
                    issues.append(
                        ValidationIssue(
                            "invalid_event", f"{behavior_location}.event", "Invalid trigger event"
                        )
                    )
                if graph_id is not None and graph_id not in graph_ids:
                    issues.append(
                        ValidationIssue(
                            "dangling_behavior_graph",
                            f"{behavior_location}.graphId",
                            f"Behavior references missing graph: {graph_id}",
                        )
                    )
                if target is not None and graph_id in graph_node_ids:
                    trigger_targets = {
                        value for value in (_trigger_target(graph_node_ids[graph_id], graphs or []))
                    }
                    if target not in trigger_targets and trigger_targets:
                        issues.append(
                            ValidationIssue(
                                "behavior_trigger_mismatch",
                                f"{behavior_location}.targetNodeId",
                                "Behavior target does not match its trigger node",
                            )
                        )

        route_parameters = screen.get("routeParameters")
        if isinstance(route_parameters, list):
            for parameter_index, raw_parameter in enumerate(route_parameters):
                parameter_location = f"{location}.routeParameters[{parameter_index}]"
                parameter = _as_object(raw_parameter, parameter_location, issues)
                if parameter is None:
                    continue
                source = parameter.get("source")
                if isinstance(source, dict):
                    collection_id = source.get("collectionId")
                    identifier_key = source.get("identifierKey")
                    if isinstance(collection_id, str) and collection_id not in collection_ids:
                        issues.append(
                            ValidationIssue(
                                "invalid_collection_reference",
                                f"{parameter_location}.source.collectionId",
                                f"Collection not found: {collection_id}",
                            )
                        )
                    if (
                        isinstance(collection_id, str)
                        and isinstance(identifier_key, str)
                        and identifiers.get(collection_id) != identifier_key
                    ):
                        issues.append(
                            ValidationIssue(
                                "invalid_identifier_key",
                                f"{parameter_location}.source.identifierKey",
                                "Route parameter identifier does not match the collection",
                            )
                        )

    if node_count > MAX_NODES:
        issues.append(
            ValidationIssue(
                "too_many_nodes", "project.screens", f"At most {MAX_NODES} nodes are supported"
            )
        )
    return screen_paths, {screen_id: screen_id for screen_id in screen_ids}, deferred_actions


def _trigger_target(graph_node_ids: set[str], graphs: list[object]) -> set[str]:
    targets: set[str] = set()
    for raw_graph in graphs:
        if not isinstance(raw_graph, dict):
            continue
        graph = cast(dict[str, object], raw_graph)
        nodes = graph.get("nodes")
        if not isinstance(nodes, list):
            continue
        for raw_node in nodes:
            if not isinstance(raw_node, dict):
                continue
            node = cast(dict[str, object], raw_node)
            if node.get("kind") != "trigger" or node.get("id") not in graph_node_ids:
                continue
            target = node.get("targetNodeId")
            if isinstance(target, str):
                targets.add(target)
    return targets


def _validate_node_references(
    node: dict[str, object],
    location: str,
    node_id: str | None,
    collection_ids: set[str],
    field_ids: dict[str, set[str]],
    identifiers: dict[str, str],
    issues: list[ValidationIssue],
) -> None:
    props = node.get("props")
    if isinstance(props, dict):
        source = props.get("dataSource")
        if isinstance(source, dict) and source.get("kind") == "collection":
            collection_id = source.get("collectionId")
            if isinstance(collection_id, str) and collection_id not in collection_ids:
                issues.append(
                    ValidationIssue(
                        "invalid_collection_reference",
                        f"{location}.props.dataSource.collectionId",
                        f"Collection not found: {collection_id}",
                    )
                )
            else:
                display_fields = source.get("displayFieldIds")
                if isinstance(display_fields, list):
                    for index, field_id in enumerate(display_fields):
                        if isinstance(field_id, str) and isinstance(collection_id, str):
                            if field_id not in field_ids.get(collection_id, set()):
                                issues.append(
                                    ValidationIssue(
                                        "invalid_field_reference",
                                        f"{location}.props.dataSource.displayFieldIds[{index}]",
                                        f"Field not found in collection: {field_id}",
                                    )
                                )
        payload = props.get("payload")
        if isinstance(payload, dict):
            _validate_action_payload(
                payload, f"{location}.props.payload", collection_ids, field_ids, issues
            )

    bindings = node.get("bindings")
    if isinstance(bindings, list):
        for index, raw_binding in enumerate(bindings):
            binding_location = f"{location}.bindings[{index}]"
            binding = _as_object(raw_binding, binding_location, issues)
            if binding is None:
                continue
            value = binding.get("value")
            if isinstance(value, dict) and value.get("source") == "list-item":
                field_id = value.get("fieldId")
                if isinstance(field_id, str):
                    available = set().union(*field_ids.values()) if field_ids else set()
                    if available and field_id not in available:
                        issues.append(
                            ValidationIssue(
                                "invalid_field_reference",
                                f"{binding_location}.value.fieldId",
                                f"Field not found in any collection: {field_id}",
                            )
                        )

    item_frame = node.get("itemFrame")
    if isinstance(item_frame, dict):
        identity = item_frame.get("recordIdentity")
        if isinstance(identity, dict):
            collection_id = identity.get("collectionId")
            key = identity.get("key")
            if isinstance(collection_id, str) and isinstance(key, str):
                if collection_id not in collection_ids or key != identifiers.get(collection_id):
                    issues.append(
                        ValidationIssue(
                            "invalid_record_identity",
                            f"{location}.itemFrame.recordIdentity",
                            "Record identity does not match a declared collection",
                        )
                    )


def _validate_action_payload(
    payload: dict[str, object],
    location: str,
    collection_ids: set[str],
    field_ids: dict[str, set[str]],
    issues: list[ValidationIssue],
) -> None:
    action_type = payload.get("type")
    if not isinstance(action_type, str):
        issues.append(ValidationIssue("invalid_action", location, "Action type is required"))
        return
    if action_type in DEFERRED_ACTION_TYPES:
        return
    if action_type not in SUPPORTED_ACTION_TYPES:
        issues.append(
            ValidationIssue("unsupported_action", location, f"Unsupported action: {action_type}")
        )
        return
    collection_id = payload.get("collectionId")
    if isinstance(collection_id, str) and collection_id not in collection_ids:
        issues.append(
            ValidationIssue(
                "invalid_collection_reference",
                f"{location}.collectionId",
                f"Collection not found: {collection_id}",
            )
        )
    values = payload.get("values")
    if isinstance(values, dict) and isinstance(collection_id, str):
        for field_id in values:
            if field_id not in field_ids.get(collection_id, set()):
                issues.append(
                    ValidationIssue(
                        "invalid_field_reference",
                        f"{location}.values.{field_id}",
                        f"Field not found in collection: {field_id}",
                    )
                )


def _validate_logic_node(
    node: dict[str, object],
    location: str,
    collection_ids: set[str],
    field_ids: dict[str, set[str]],
    issues: list[ValidationIssue],
) -> list[str]:
    kind = node.get("kind")
    if kind == "trigger":
        event = node.get("event")
        if event not in _EVENTS:
            issues.append(
                ValidationIssue("invalid_event", f"{location}.event", "Invalid trigger event")
            )
        return []
    if kind == "condition":
        operator = node.get("operator")
        if operator not in (
            "equals",
            "not-equals",
            "contains",
            "not-contains",
            "is-empty",
            "is-not-empty",
        ):
            issues.append(
                ValidationIssue(
                    "invalid_condition", f"{location}.operator", "Invalid condition operator"
                )
            )
        return []
    if kind == "action":
        payload = _as_object(node.get("payload"), f"{location}.payload", issues)
        if payload is not None:
            action_type = payload.get("type")
            _validate_action_payload(
                payload, f"{location}.payload", collection_ids, field_ids, issues
            )
            if isinstance(action_type, str) and action_type in DEFERRED_ACTION_TYPES:
                return [action_type]
    return []


def _validate_layout(
    layout: dict[str, object],
    screen_paths: dict[str, str],
    issues: list[ValidationIssue],
) -> None:
    if layout.get("layoutVersion") != 2:
        issues.append(
            ValidationIssue(
                "invalid_layout_version", "layout.layoutVersion", "Unsupported layout version"
            )
        )
    screens = _as_list(layout.get("screens"), "layout.screens", issues)
    if screens is None:
        return
    layout_screens: dict[str, str] = {}
    for index, raw_screen in enumerate(screens):
        location = f"layout.screens[{index}]"
        screen = _as_object(raw_screen, location, issues)
        if screen is None:
            continue
        screen_id = _as_text(screen.get("screenId"), f"{location}.screenId", issues)
        path_value = screen.get("path")
        if screen_id is not None:
            if screen_id in layout_screens:
                issues.append(
                    ValidationIssue(
                        "duplicate_layout_screen", location, f"Duplicate screen: {screen_id}"
                    )
                )
            if isinstance(path_value, str):
                layout_screens[screen_id] = path_value
    if set(layout_screens) != set(screen_paths):
        issues.append(
            ValidationIssue(
                "layout_screen_mismatch",
                "layout.screens",
                "Layout screens must match project screens exactly",
            )
        )
        return
    for screen_id, path_value in screen_paths.items():
        if layout_screens.get(screen_id) != path_value:
            issues.append(
                ValidationIssue(
                    "layout_route_mismatch",
                    f"layout.screens.{screen_id}.path",
                    "Layout screen path does not match the project screen path",
                )
            )


def _validate_runtime(
    runtime: dict[str, object], screen_paths: dict[str, str], issues: list[ValidationIssue]
) -> tuple[list[str], list[str]]:
    deferred: list[str] = []
    required_by: list[str] = []
    requirements = _as_list(runtime.get("requirements"), "runtime.requirements", issues)
    if requirements is not None:
        for index, raw_requirement in enumerate(requirements):
            location = f"runtime.requirements[{index}]"
            requirement = _as_object(raw_requirement, location, issues)
            if requirement is None:
                continue
            capability = _as_text(requirement.get("capability"), f"{location}.capability", issues)
            if capability is None:
                continue
            if capability not in SUPPORTED_RUNTIME_CAPABILITIES:
                issues.append(
                    ValidationIssue(
                        "unknown_capability",
                        f"{location}.capability",
                        f"Unknown capability: {capability}",
                    )
                )
            elif capability in DEFERRED_CAPABILITIES:
                deferred.append(capability)
                sources = requirement.get("requiredBy")
                if isinstance(sources, list):
                    required_by.extend(str(source) for source in sources)
            _as_text(requirement.get("requiredBinding"), f"{location}.requiredBinding", issues)

    routes = _as_list(runtime.get("routes"), "runtime.routes", issues)
    if routes is not None:
        for index, raw_route in enumerate(routes):
            location = f"runtime.routes[{index}]"
            route = _as_object(raw_route, location, issues)
            if route is None:
                continue
            screen_id = _as_text(route.get("screenId"), f"{location}.screenId", issues)
            path_value = route.get("path")
            if screen_id is not None and screen_id not in screen_paths:
                issues.append(
                    ValidationIssue(
                        "unknown_route_screen", location, f"Unknown screen: {screen_id}"
                    )
                )
            elif screen_id is not None and path_value != screen_paths[screen_id]:
                issues.append(
                    ValidationIssue(
                        "route_path_mismatch", location, "Route path does not match the screen"
                    )
                )

    breakpoints = runtime.get("responsiveBreakpoints")
    if breakpoints != {"tabletMinWidth": 768, "desktopMinWidth": 1024}:
        issues.append(
            ValidationIssue(
                "invalid_breakpoints",
                "runtime.responsiveBreakpoints",
                "Responsive breakpoints must be 768 and 1024",
            )
        )
    return deferred, required_by


def validate_manifest(value: object) -> dict[str, object]:
    """Validate a parsed manifest and return it as a typed mapping.

    Raises `InvalidManifestError` with all collected issues, or
    `UnsupportedCapabilityError` when the manifest is well-formed but declares
    a deferred capability.
    """
    issues: list[ValidationIssue] = []
    envelope = _as_object(value, "manifest", issues)
    if envelope is None:
        raise InvalidManifestError(issues)

    missing = sorted(_ENVELOPE_KEYS - set(envelope))
    unexpected = sorted(set(envelope) - _ENVELOPE_KEYS)
    for key in missing:
        issues.append(ValidationIssue("missing_field", key, f"Missing required field: {key}"))
    for key in unexpected:
        issues.append(ValidationIssue("unexpected_field", key, f"Unexpected field: {key}"))

    raw_version = envelope.get("manifestVersion")
    version = _as_int(raw_version, "manifestVersion", issues)
    if version is not None and version != 2:
        issues.append(
            ValidationIssue(
                "unsupported_manifest_version", "manifestVersion", "Only manifest v2 is supported"
            )
        )
    elif version is None and raw_version is not None:
        issues.append(
            ValidationIssue(
                "unsupported_manifest_version", "manifestVersion", "Only manifest v2 is supported"
            )
        )
    generated_at = _as_text(envelope.get("generatedAt"), "generatedAt", issues)
    if generated_at is not None:
        try:
            datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            issues.append(
                ValidationIssue("invalid_timestamp", "generatedAt", "generatedAt must be ISO-8601")
            )

    _scan_for_sensitive_content(envelope, "manifest", 0, issues)

    security = _as_object(envelope.get("security"), "security", issues)
    if security is not None:
        for key in ("secretsIncluded", "rawMediaIncluded", "biometricTemplatesIncluded"):
            flag = _as_bool(security.get(key), f"security.{key}", issues)
            if flag is True:
                issues.append(
                    ValidationIssue("sensitive_content", f"security.{key}", f"{key} must be false")
                )

    app_data = _as_object(envelope.get("appData"), "appData", issues)
    if app_data is not None:
        records_included = _as_bool(
            app_data.get("recordsIncluded"), "appData.recordsIncluded", issues
        )
        if records_included is True:
            issues.append(
                ValidationIssue(
                    "records_included",
                    "appData.recordsIncluded",
                    "App records must not be exported",
                )
            )
    collection_ids, field_ids, identifiers = (
        _validate_collections(app_data, issues) if app_data is not None else (set(), {}, {})
    )

    project = _as_object(envelope.get("project"), "project", issues)
    screen_paths: dict[str, str] = {}
    deferred_actions: list[str] = []
    if project is not None:
        screen_paths, _, deferred_actions = _validate_project(
            project, collection_ids, field_ids, identifiers, issues
        )

    layout = _as_object(envelope.get("layout"), "layout", issues)
    if layout is not None:
        _validate_layout(layout, screen_paths, issues)

    deferred: list[str] = []
    required_by: list[str] = []
    runtime = _as_object(envelope.get("runtime"), "runtime", issues)
    if runtime is not None:
        deferred, required_by = _validate_runtime(runtime, screen_paths, issues)

    if issues:
        raise InvalidManifestError(issues)
    unsupported = sorted(set(deferred) | set(deferred_actions))
    if unsupported:
        raise UnsupportedCapabilityError(unsupported, sorted(set(required_by)))
    return envelope
