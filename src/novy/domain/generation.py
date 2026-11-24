"""Pure domain models and policy for React source generation.

This module deliberately has no FastAPI, Pydantic, MongoDB or I/O dependencies.
It defines the generated-source value objects, the generator support policy and
path/name helpers used by the application and infrastructure layers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class GenerationError(Exception):
    """Base class for safe, generation-owned failures."""


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single manifest validation issue with a stable code and location."""

    code: str
    location: str
    message: str


class InvalidManifestError(GenerationError):
    """The submitted manifest is malformed, unsafe or inconsistent."""

    def __init__(self, issues: list[ValidationIssue] | None = None) -> None:
        self.issues = issues or []
        super().__init__(f"Invalid manifest ({len(self.issues)} issue(s))")


class UnsupportedCapabilityError(GenerationError):
    """The manifest declares a capability the generator must not emit."""

    def __init__(self, capabilities: list[str], required_by: list[str] | None = None) -> None:
        self.capabilities = capabilities
        self.required_by = required_by or []
        super().__init__(f"Unsupported generation capability: {', '.join(capabilities)}")


class GenerationTimeoutError(GenerationError):
    """Generation exceeded its deadline."""


# Every component DTO type the generator knows how to render.
SUPPORTED_COMPONENT_TYPES = frozenset(
    {
        "button",
        "text",
        "icon",
        "input",
        "image",
        "camera",
        "file-input",
        "chat",
        "divider",
        "checkbox",
        "radio",
        "switch",
        "dropdown",
        "slider",
        "progress",
        "listview",
        "chart",
        "container",
        "gesture-detector",
        "column",
        "row",
    }
)

# Logic actions emitted as local/browser behavior or as a runtime client call.
SUPPORTED_ACTION_TYPES = frozenset(
    {
        "change-text",
        "show-message",
        "navigate",
        "get-records",
        "get-record",
        "insert-record",
        "update-record",
        "delete-record",
        "refresh-collection",
        "load-list-items",
        "load-chart-data",
        "set-variable",
        "set-theme",
        "set-component-value",
        "add-local-list-item",
        "send-email",
    }
)

# AI/RAG/CV nodes are authoring-only until the runtime surface exists.
DEFERRED_ACTION_TYPES = frozenset(
    {
        "document-upload",
        "chat-message",
        "document-load",
        "text-split",
        "embedding-generate",
        "vector-upsert",
        "vector-delete",
        "chat-input",
        "query-embedding",
        "vector-retrieve",
        "rerank",
        "prompt-template",
        "llm-generate",
        "chat-output",
        "memory",
        "vector-store",
        "chat-model",
        "ai-agent",
        "cv-liveness",
        "cv-enroll",
        "cv-verify",
    }
)

BACKEND_CAPABILITIES = frozenset({"database", "email", "deployment"})
BROWSER_CAPABILITIES = frozenset({"camera"})
DEFERRED_CAPABILITIES = frozenset({"rag", "computer-vision", "authentication"})
SUPPORTED_RUNTIME_CAPABILITIES = BACKEND_CAPABILITIES | BROWSER_CAPABILITIES | DEFERRED_CAPABILITIES


def safe_relative_path(path: str) -> str:
    """Validate a generated-file path; reject absolute or traversing paths."""
    if not path or len(path) > 255:
        raise ValueError("Invalid generated path")
    if any(ord(character) < 32 for character in path):
        raise ValueError("Invalid generated path")
    if path.startswith("/") or "\\" in path or re.match(r"^[A-Za-z]:", path) is not None:
        raise ValueError("Invalid generated path")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Invalid generated path")
    return path


def slug_for(name: str) -> str:
    """Build a filesystem-safe, frontend-acceptable archive slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:48].rstrip("-") or "novy-project"


def identifier_for(name: str) -> str:
    """Build a PascalCase TypeScript identifier from a human-readable name."""
    parts = re.findall(r"[A-Za-z0-9]+", name)
    identifier = "".join(part[:1].upper() + part[1:] for part in parts) or "Generated"
    return identifier if not identifier[0].isdigit() else f"N{identifier}"


@dataclass(frozen=True, slots=True)
class GeneratedFile:
    """One deterministic source file in the export archive."""

    path: str
    content: str

    def __post_init__(self) -> None:
        safe_relative_path(self.path)


@dataclass(frozen=True, slots=True)
class GeneratedArchive:
    """A named, ordered set of generated source files."""

    filename: str
    files: tuple[GeneratedFile, ...]

    def __post_init__(self) -> None:
        if not self.filename or "/" in self.filename or "\\" in self.filename:
            raise ValueError("Invalid archive filename")
        if re.fullmatch(r"[A-Za-z0-9._-]+\.zip", self.filename) is None:
            raise ValueError("Invalid archive filename")
