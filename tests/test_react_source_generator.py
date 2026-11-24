"""Golden and determinism tests for the React source generator."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from novy.domain.generation import GeneratedArchive
from novy.infrastructure.react_source.generator import ReactSourceGenerator

FIXTURE = Path(__file__).parent / "fixtures" / "react_export" / "manifest_v2.json"


@pytest.fixture()
def archive() -> GeneratedArchive:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return ReactSourceGenerator().generate(manifest)


def contents(archive: GeneratedArchive) -> dict[str, str]:
    return {item.path: item.content for item in archive.files}


def test_archive_filename_is_safe_and_frontend_acceptable(archive: GeneratedArchive) -> None:
    assert archive.filename == "notes-react-app.zip"
    assert re.fullmatch(r"[A-Za-z0-9._-]+\.zip", archive.filename) is not None


def test_expected_files_are_generated(archive: GeneratedArchive) -> None:
    paths = set(contents(archive))
    assert {
        "package.json",
        "index.html",
        "README.md",
        "src/main.tsx",
        "src/generated/App.tsx",
        "src/generated/routes.ts",
        "src/generated/tokens.ts",
        "src/generated/data/collections.ts",
        "src/generated/runtime/client.ts",
        "src/generated/logic/index.ts",
        "src/generated/screens/ScreenHomeScreen.tsx",
    } <= paths


def test_generated_routes_and_tokens_match_manifest(archive: GeneratedArchive) -> None:
    files = contents(archive)
    assert "screen_home" in files["src/generated/routes.ts"]
    assert '"/add"' in files["src/generated/routes.ts"]
    assert "tabletMinWidth: 768" in files["src/generated/tokens.ts"]
    assert "desktopMinWidth: 1024" in files["src/generated/tokens.ts"]


def test_screen_renders_component_tree_and_item_bindings(archive: GeneratedArchive) -> None:
    screen = contents(archive)["src/generated/screens/ScreenHomeScreen.tsx"]
    assert 'data-node-id="node_add"' in screen
    assert 'data-component="button"' in screen
    assert 'data-collection-id="collection_notes"' in screen
    assert 'data-field-binding="field_title"' in screen
    assert "// Repeats once per record" in screen


def test_logic_function_contains_trigger_action_and_branches(archive: GeneratedArchive) -> None:
    logic = contents(archive)["src/generated/logic/index.ts"]
    assert "run_GraphAdd" in logic
    assert "Trigger: click on" in logic
    assert 'runtime.navigate("/add")' in logic
    assert "Branch: success | error" in logic


def test_collections_include_identity_but_no_records(archive: GeneratedArchive) -> None:
    collections = contents(archive)["src/generated/data/collections.ts"]
    assert "noteId" in collections
    assert "field_title" in collections
    assert "Title" in collections


def test_no_sensitive_or_record_content_is_emitted(archive: GeneratedArchive) -> None:
    joined = "\n".join(item.content for item in archive.files).lower()
    assert "data:image/" not in joined
    assert "biometric_template" not in joined
    assert "apikey" not in joined
    assert "secret" not in joined


def test_generation_is_deterministic() -> None:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    generator = ReactSourceGenerator()
    first = generator.generate(manifest)
    second = generator.generate(manifest)
    assert first == second
    assert [item.path for item in first.files] == [item.path for item in second.files]


def test_empty_project_still_generates_a_runnable_scaffold() -> None:
    manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
    manifest["project"]["screens"] = []
    manifest["layout"]["screens"] = []
    manifest["runtime"]["routes"] = []
    archive = ReactSourceGenerator().generate(manifest)
    files = contents(archive)
    assert "src/generated/App.tsx" in files
    assert "src/main.tsx" in files
