"""Strict validation of the v2 React export manifest."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from novy.domain.generation import (
    InvalidManifestError,
    UnsupportedCapabilityError,
    identifier_for,
    safe_relative_path,
    slug_for,
)
from novy.domain.manifest_validation import validate_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "react_export"


def load_fixture(name: str = "manifest_v2.json") -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def codes(exc: InvalidManifestError) -> set[str]:
    return {issue.code for issue in exc.issues}


def test_valid_manifest_passes_validation() -> None:
    assert validate_manifest(load_fixture())["manifestVersion"] == 2


def test_invalid_fixture_reports_version_and_privacy_issues() -> None:
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(load_fixture("manifest_v2_invalid.json"))
    assert {
        "unsupported_manifest_version",
        "records_included",
        "sensitive_content",
        "dangling_behavior_graph",
    } <= codes(caught.value)


def test_non_object_manifest_is_rejected() -> None:
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(["not", "an", "object"])
    assert "invalid_type" in codes(caught.value)


@pytest.mark.parametrize("bad_version", [1, 3, "2", True])
def test_only_manifest_v2_is_accepted(bad_version: object) -> None:
    manifest = load_fixture()
    manifest["manifestVersion"] = bad_version
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "unsupported_manifest_version" in codes(caught.value)


def test_missing_envelope_field_is_rejected() -> None:
    manifest = load_fixture()
    del manifest["runtime"]
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "missing_field" in codes(caught.value)


def test_unknown_envelope_field_is_rejected() -> None:
    manifest = load_fixture()
    manifest["unexpected"] = True
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "unexpected_field" in codes(caught.value)


def test_oversized_string_is_rejected() -> None:
    manifest = load_fixture()
    manifest["project"]["name"] = "x" * 20_001
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "invalid_value" in codes(caught.value)


def test_security_flags_must_be_false() -> None:
    manifest = load_fixture()
    manifest["security"]["secretsIncluded"] = True
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "sensitive_content" in codes(caught.value)


def test_records_must_not_be_included() -> None:
    manifest = load_fixture()
    manifest["appData"]["recordsIncluded"] = True
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "records_included" in codes(caught.value)


def test_secret_keys_and_raw_media_values_are_rejected() -> None:
    manifest = load_fixture()
    manifest["project"]["screens"][0]["nodes"][0]["props"]["apiKey"] = "abc"
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "sensitive_key" in codes(caught.value)

    media_manifest = load_fixture()
    media_manifest["project"]["screens"][0]["nodes"][0]["props"]["text"] = (
        "data:image/png;base64,AAA"
    )
    with pytest.raises(InvalidManifestError) as caught_media:
        validate_manifest(media_manifest)
    assert "sensitive_value" in codes(caught_media.value)


def test_duplicate_screen_ids_are_rejected() -> None:
    manifest = load_fixture()
    manifest["project"]["screens"][1]["id"] = "screen_home"
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "duplicate_screen_id" in codes(caught.value)


def test_dangling_behavior_graph_is_rejected() -> None:
    manifest = load_fixture()
    manifest["project"]["screens"][0]["behaviors"][0]["graphId"] = "graph_missing"
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "dangling_behavior_graph" in codes(caught.value)


def test_invalid_edge_port_is_rejected() -> None:
    manifest = load_fixture()
    manifest["project"]["screens"][0]["logicGraphs"][0]["edges"][0]["fromPort"] = "sideways"
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "invalid_edge_port" in codes(caught.value)


def test_unknown_component_type_is_rejected() -> None:
    manifest = load_fixture()
    manifest["project"]["screens"][0]["nodes"][0]["type"] = "mystery-widget"
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "unsupported_component_type" in codes(caught.value)


def test_unknown_collection_reference_is_rejected() -> None:
    manifest = load_fixture()
    manifest["project"]["screens"][0]["nodes"][2]["props"]["dataSource"]["collectionId"] = "missing"
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "invalid_collection_reference" in codes(caught.value)


def test_layout_route_mismatch_is_rejected() -> None:
    manifest = load_fixture()
    manifest["layout"]["screens"][0]["path"] = "/different"
    with pytest.raises(InvalidManifestError) as caught:
        validate_manifest(manifest)
    assert "layout_route_mismatch" in codes(caught.value)


def test_deferred_capability_raises_specific_error() -> None:
    manifest = load_fixture()
    manifest["runtime"]["requirements"].append(
        {
            "capability": "rag",
            "configurationOwner": "backend",
            "requiredBinding": "rag.knowledge-index",
            "requiredBy": ["node_list"],
        }
    )
    with pytest.raises(UnsupportedCapabilityError) as caught:
        validate_manifest(manifest)
    assert caught.value.capabilities == ["rag"]
    assert caught.value.required_by == ["node_list"]


def test_deferred_action_type_is_rejected_as_unsupported() -> None:
    manifest = load_fixture()
    node = deepcopy(manifest["project"]["screens"][0]["logicGraphs"][0]["nodes"][1])
    node["id"] = "node_cv"
    node["payload"] = {"type": "cv-verify"}
    manifest["project"]["screens"][0]["logicGraphs"][0]["nodes"].append(node)
    with pytest.raises(UnsupportedCapabilityError) as caught:
        validate_manifest(manifest)
    assert caught.value.capabilities == ["cv-verify"]


@pytest.mark.parametrize(
    "bad_path",
    ["../etc/passwd", "/etc/passwd", "src/../../secret", "C:\\temp\\file", "src//file", ""],
)
def test_safe_relative_path_rejects_traversal(bad_path: str) -> None:
    with pytest.raises(ValueError):
        safe_relative_path(bad_path)


def test_safe_relative_path_keeps_valid_paths() -> None:
    assert safe_relative_path("src/generated/App.tsx") == "src/generated/App.tsx"


def test_slug_and_identifier_helpers() -> None:
    assert slug_for("My Notes App!") == "my-notes-app"
    assert slug_for("!!!") == "novy-project"
    assert identifier_for("Add note") == "AddNote"
    assert identifier_for("2fa settings") == "N2faSettings"
