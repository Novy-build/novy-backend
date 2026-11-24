"""Deterministic React/Vite source generator for manifest v2 exports.

All output is derived only from the validated manifest. No clock, UUID, network
call or record data enters the generated source.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from novy.domain.generation import GeneratedArchive, GeneratedFile, identifier_for, slug_for

_TEMPLATES = Path(__file__).parent / "templates"

_STATIC_FILES = (
    "package.json",
    "tsconfig.json",
    "tsconfig.node.json",
    "vite.config.ts",
    "index.html",
    ".gitignore",
    "README.md",
)

_VOID_TAGS = frozenset({"input", "img", "hr"})

_TAGS = {
    "text": "p",
    "button": "button",
    "input": "input",
    "image": "img",
    "divider": "hr",
    "icon": "span",
    "camera": "div",
    "file-input": "input",
    "chat": "div",
    "chart": "div",
    "container": "div",
    "column": "div",
    "row": "div",
    "gesture-detector": "div",
    "listview": "section",
    "checkbox": "input",
    "radio": "div",
    "switch": "button",
    "dropdown": "select",
    "slider": "input",
    "progress": "progress",
}


def _object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _number(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _ts(value: object) -> str:
    """Emit a safe TypeScript string literal."""
    return json.dumps(value if isinstance(value, str) else str(value), ensure_ascii=True)


def _binding(node: dict[str, object]) -> str:
    for binding in _list(node.get("bindings")):
        raw = _object(binding).get("value")
        value = _object(raw)
        if value.get("source") == "list-item" and isinstance(value.get("fieldId"), str):
            return f" data-field-binding={_ts(value['fieldId'])}"
    return ""


def _render_node(node: dict[str, object], indent: str) -> list[str]:
    node_type = _text(node.get("type") or node.get("kind"), "container")
    tag = _TAGS.get(node_type, "div")
    props = _object(node.get("props"))
    node_id = _text(node.get("id"), "node")
    style = (
        "{ position: 'absolute', left: "
        + str(_number(node.get("x")))
        + ", top: "
        + str(_number(node.get("y")))
        + ", width: "
        + str(_number(node.get("w")))
        + ", height: "
        + str(_number(node.get("h")))
        + " }"
    )
    attrs = (
        f"data-node-id={_ts(node_id)} data-component={_ts(node_type)}"
        f"{_binding(node)} style={{{{{style}}}}}"
    )

    if node_type == "button":
        attrs += ' type="button"'
        content = _ts(_text(props.get("label"), "Button"))
    elif node_type == "text":
        content = _ts(_text(props.get("text"), "Text"))
    elif node_type == "input":
        content = ""
        attrs += f" placeholder={_ts(_text(props.get('placeholder')))}"
    elif node_type == "image":
        content = ""
        attrs += f" alt={_ts(_text(props.get('alt'), 'Image'))}"
    elif node_type in ("checkbox", "slider"):
        content = ""
        attrs += ' type="' + ("checkbox" if node_type == "checkbox" else "range") + '"'
    elif node_type == "listview":
        content = ""
        source = _object(props.get("dataSource"))
        collection_id = _text(source.get("collectionId"))
        if collection_id:
            attrs += f" data-collection-id={_ts(collection_id)}"
    else:
        content = ""

    if node_type == "listview":
        item_frame = _object(node.get("itemFrame"))
        source_id = _text(source.get("collectionId")) or "static items"
        lines = [f"{indent}// Repeats once per record from {_ts(source_id)}."]
        lines.append(f"{indent}<{tag} {attrs}>")
        for child in _list(item_frame.get("children")):
            lines.extend(_render_node(_object(child), indent + "  "))
        lines.append(f"{indent}</{tag}>")
        return lines

    if tag in _VOID_TAGS:
        return [f"{indent}<{tag} {attrs} />"]

    lines = [f"{indent}<{tag} {attrs}>"]
    if content:
        lines.append(f"{indent}  {content}")
    for child in _list(node.get("children")):
        lines.extend(_render_node(_object(child), indent + "  "))
    lines.append(f"{indent}</{tag}>")
    return lines


def _render_tokens(manifest: dict[str, object], breakpoints: dict[str, object]) -> str:
    layout = _object(manifest.get("layout"))
    design_system = layout.get("designSystem")
    design_json = json.dumps(
        design_system if isinstance(design_system, dict) else {}, indent=2, ensure_ascii=True
    )
    return (
        "export const breakpoints = {\n"
        f"  tabletMinWidth: {_number(breakpoints.get('tabletMinWidth'), 768)},\n"
        f"  desktopMinWidth: {_number(breakpoints.get('desktopMinWidth'), 1024)},\n"
        "} as const;\n\n"
        f"export const designSystem = {design_json} as const;\n"
    )


def _render_routes(manifest: dict[str, object]) -> tuple[str, dict[str, str]]:
    runtime = _object(manifest.get("runtime"))
    lines = [
        "export interface GeneratedRoute {",
        "  screenId: string;",
        "  path: string;",
        "  parameters?: Array<{ name: string; collectionId: string; identifierKey: string }>;",
        "}",
        "",
        "export const routes: GeneratedRoute[] = [",
    ]
    paths: dict[str, str] = {}
    for raw_route in _list(runtime.get("routes")):
        route = _object(raw_route)
        screen_id = _text(route.get("screenId"))
        path = _text(route.get("path"))
        paths[screen_id] = path
        lines.append("  {")
        lines.append(f"    screenId: {_ts(screen_id)},")
        lines.append(f"    path: {_ts(path)},")
        parameters = _list(route.get("parameters"))
        if parameters:
            lines.append("    parameters: [")
            for raw_parameter in parameters:
                parameter = _object(raw_parameter)
                lines.append(
                    "      { name: "
                    + _ts(_text(parameter.get("name")))
                    + ", collectionId: "
                    + _ts(_text(parameter.get("collectionId")))
                    + ", identifierKey: "
                    + _ts(_text(parameter.get("identifierKey")))
                    + " },"
                )
            lines.append("    ],")
        lines.append("  },")
    lines.append("];")
    lines.append("")
    return "\n".join(lines), paths


def _render_collections(manifest: dict[str, object]) -> str:
    app_data = _object(manifest.get("appData"))
    lines = [
        "export interface GeneratedField {",
        "  id: string;",
        "  key: string;",
        "  label: string;",
        "  type: string;",
        "  required: boolean;",
        "}",
        "",
        "export interface GeneratedCollection {",
        "  id: string;",
        "  name: string;",
        "  identifierKey: string;",
        "  fields: GeneratedField[];",
        "}",
        "",
        "export const collections: GeneratedCollection[] = [",
    ]
    for raw_collection in _list(app_data.get("collections")):
        collection = _object(raw_collection)
        identifier = _object(collection.get("identifier"))
        lines.append("  {")
        lines.append(f"    id: {_ts(_text(collection.get('id')))},")
        lines.append(f"    name: {_ts(_text(collection.get('name')))},")
        lines.append(f"    identifierKey: {_ts(_text(identifier.get('key')))},")
        lines.append("    fields: [")
        for raw_field in _list(collection.get("fields")):
            field = _object(raw_field)
            lines.append(
                "      { id: "
                + _ts(_text(field.get("id")))
                + ", key: "
                + _ts(_text(field.get("key")))
                + ", label: "
                + _ts(_text(field.get("label")))
                + ", type: "
                + _ts(_text(field.get("type")))
                + ", required: "
                + ("true" if field.get("required") is True else "false")
                + " },"
            )
        lines.append("    ],")
        lines.append("  },")
    lines.append("];")
    lines.append("")
    return "\n".join(lines)


def _action_call(payload: dict[str, object], paths: dict[str, str]) -> str:
    action_type = _text(payload.get("type"))
    if action_type == "navigate":
        target = _text(payload.get("targetScreenId"))
        return f"  await runtime.navigate({_ts(paths.get(target, '/'))});"
    if action_type == "change-text":
        return (
            f"  runtime.setText({_ts(_text(payload.get('targetNodeId')))}, "
            f"{_ts(_text(payload.get('text')))});"
        )
    if action_type == "show-message":
        return f"  runtime.showMessage({_ts(_text(payload.get('message')))});"
    if action_type in (
        "insert-record",
        "update-record",
        "delete-record",
        "get-records",
        "get-record",
    ):
        method = {
            "insert-record": "insertRecord",
            "update-record": "updateRecord",
            "delete-record": "deleteRecord",
            "get-records": "listRecords",
            "get-record": "getRecord",
        }[action_type]
        return (
            f"  await runtime.{method}({_ts(_text(payload.get('collectionId')))}, context.inputs);"
        )
    if action_type == "send-email":
        return "  await runtime.sendEmail(context.inputs);"
    if action_type == "refresh-collection":
        return f"  runtime.refreshCollection({_ts(_text(payload.get('collectionId')))});"
    return f"  // {action_type} requires the generated-app runtime surface."


def _render_logic(manifest: dict[str, object], paths: dict[str, str]) -> str:
    project = _object(manifest.get("project"))
    lines = [
        "import { runtime } from './runtime/client';",
        "",
        "export interface LogicContext {",
        "  inputs: Record<string, string | number | boolean | null>;",
        "}",
        "",
    ]
    for screen in _list(project.get("screens")):
        screen_data = _object(screen)
        for raw_graph in _list(screen_data.get("logicGraphs")):
            graph = _object(raw_graph)
            graph_id = _text(graph.get("id"), "graph")
            function_name = f"run_{identifier_for(graph_id)}"
            lines.append(
                f"export async function {function_name}(context: LogicContext): Promise<void> {{"
            )
            trigger_node = next(
                (
                    _object(node)
                    for node in _list(graph.get("nodes"))
                    if _object(node).get("kind") == "trigger"
                ),
                {},
            )
            lines.append(
                "  // Trigger: "
                + _text(trigger_node.get("event"), "click")
                + " on "
                + _ts(_text(trigger_node.get("targetNodeId")))
            )
            for raw_node in _list(graph.get("nodes")):
                node = _object(raw_node)
                if node.get("kind") != "action":
                    continue
                lines.append(_action_call(_object(node.get("payload")), paths))
                lines.append("  // Branch: success | error")
            lines.append("}")
            lines.append("")
    if len(lines) == 5:
        lines.append("// No logic graphs were declared in this export.")
        lines.append("")
    return "\n".join(lines)


def _render_runtime() -> str:
    return (
        "const baseUrl = import.meta.env.VITE_NOVY_RUNTIME_BASE_URL ?? '';\n\n"
        "async function call(path: string, body?: unknown): Promise<unknown> {\n"
        "  const response = await fetch(`${baseUrl}${path}`, {\n"
        "    method: body === undefined ? 'GET' : 'POST',\n"
        "    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },\n"
        "    credentials: 'include',\n"
        "    body: body === undefined ? undefined : JSON.stringify(body),\n"
        "  });\n"
        "  if (!response.ok) throw new Error(`Runtime request failed: ${response.status}`);\n"
        "  return response.json();\n"
        "}\n\n"
        "export const runtime = {\n"
        "  navigate(path: string): void { window.history.pushState({}, '', path); },\n"
        "  setText(nodeId: string, text: string): void {\n"
        '    const element = document.querySelector(`[data-node-id="${nodeId}"]`);\n'
        "    if (element) element.textContent = text;\n"
        "  },\n"
        "  showMessage(message: string): void { window.alert(message); },\n"
        "  refreshCollection(collectionId: string): void {\n"
        "    window.dispatchEvent(new CustomEvent('novy:refresh', { detail: collectionId }));\n"
        "  },\n"
        "  listRecords(collectionId: string, _inputs?: unknown): Promise<unknown> {\n"
        "    return call(`/runtime/v1/apps/app/collections/${collectionId}/records`);\n"
        "  },\n"
        "  getRecord(collectionId: string, inputs?: unknown): Promise<unknown> {\n"
        "    return call(`/runtime/v1/apps/app/collections/${collectionId}/records`, inputs);\n"
        "  },\n"
        "  insertRecord(collectionId: string, inputs?: unknown): Promise<unknown> {\n"
        "    return call(`/runtime/v1/apps/app/collections/${collectionId}/records`, inputs);\n"
        "  },\n"
        "  updateRecord(collectionId: string, inputs?: unknown): Promise<unknown> {\n"
        "    return call(`/runtime/v1/apps/app/collections/${collectionId}/records`, inputs);\n"
        "  },\n"
        "  deleteRecord(collectionId: string, inputs?: unknown): Promise<unknown> {\n"
        "    return call(`/runtime/v1/apps/app/collections/${collectionId}/records`, inputs);\n"
        "  },\n"
        "  sendEmail(inputs?: unknown): Promise<unknown> {\n"
        "    return call('/runtime/v1/apps/app/actions/send-email/execute', inputs);\n"
        "  },\n"
        "};\n"
    )


def _render_app(screens: list[tuple[str, str]], default_path: str) -> str:
    lines = [
        "import { routes } from './generated/routes';",
        "import { useEffect, useState } from 'react';",
    ]
    for component, _ in screens:
        lines.append(f"import {{ {component} }} from './generated/screens/{component}';")
    lines.extend(
        [
            "",
            "export function App() {",
            "  const [path, setPath] = useState(window.location.pathname);",
            "  useEffect(() => {",
            "    const sync = () => setPath(window.location.pathname);",
            "    window.addEventListener('popstate', sync);",
            "    return () => window.removeEventListener('popstate', sync);",
            "  }, []);",
            "  const route = routes.find((candidate) => candidate.path === path);",
            "  const active = route?.path ?? " + _ts(default_path) + ";",
            "  const Screen = {",
        ]
    )
    for component, screen_id in screens:
        lines.append(f"    {_ts(screen_id)}: {component},")
    lines.extend(
        [
            "  }[route?.screenId ?? ''] ?? " + (screens[0][0] if screens else "null") + ";",
            '  return <main className="novy-app" data-active-route={active}>'
            + "{Screen ? <Screen /> : null}</main>;",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


class ReactSourceGenerator:
    """Generate a deterministic React/Vite source tree from a validated manifest."""

    def generate(self, manifest: dict[str, object]) -> GeneratedArchive:
        files: list[GeneratedFile] = []
        for name in _STATIC_FILES:
            files.append(
                GeneratedFile(
                    path=name,
                    content=(_TEMPLATES / name).read_text(encoding="utf-8"),
                )
            )

        project = _object(manifest.get("project"))
        layout = _object(manifest.get("layout"))
        runtime = _object(manifest.get("runtime"))
        breakpoints = _object(runtime.get("responsiveBreakpoints"))
        routes_source, paths = _render_routes(manifest)

        files.append(
            GeneratedFile("src/generated/tokens.ts", _render_tokens(manifest, breakpoints))
        )
        files.append(GeneratedFile("src/generated/routes.ts", routes_source))
        files.append(
            GeneratedFile("src/generated/data/collections.ts", _render_collections(manifest))
        )
        files.append(GeneratedFile("src/generated/runtime/client.ts", _render_runtime()))
        files.append(GeneratedFile("src/generated/logic/index.ts", _render_logic(manifest, paths)))

        screen_files: list[tuple[str, str]] = []
        for raw_layout_screen in _list(layout.get("screens")):
            layout_screen = _object(raw_layout_screen)
            screen_id = _text(layout_screen.get("screenId"))
            component = f"{identifier_for(screen_id)}Screen"
            lines = [
                "export function " + component + "() {",
                "  return (",
                f'    <div className="novy-screen" data-screen-id={_ts(screen_id)}>',
            ]
            for raw_node in _list(layout_screen.get("nodes")):
                lines.extend(_render_node(_object(raw_node), "      "))
            lines.extend(["    </div>", "  );", "}", ""])
            files.append(GeneratedFile(f"src/generated/screens/{component}.tsx", "\n".join(lines)))
            screen_files.append((component, screen_id))

        default_path = next(iter(paths.values()), "/")
        files.append(
            GeneratedFile("src/generated/App.tsx", _render_app(screen_files, default_path))
        )
        files.append(
            GeneratedFile(
                "src/main.tsx",
                "import { StrictMode } from 'react';\n"
                "import { createRoot } from 'react-dom/client';\n"
                "import { App } from './generated/App';\n\n"
                "createRoot(document.getElementById('root')!).render(\n"
                "  <StrictMode><App /></StrictMode>,\n"
                ");\n",
            )
        )

        project_name = _text(project.get("name"), "Novy app")
        filename = f"{slug_for(project_name)}-react-app.zip"
        return GeneratedArchive(filename=filename, files=tuple(files))
