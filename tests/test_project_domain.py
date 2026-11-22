"""Unit tests for the project domain models."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from novy.domain.projects import (
    DEFAULT_PROJECT_TYPE,
    LIFECYCLE_ACTIVE,
    LIFECYCLE_DELETED,
    PROJECT_TYPE_APP,
    PROJECT_TYPE_WEBSITE,
    SUPPORTED_PROJECT_TYPES,
    Project,
    ProjectDraft,
    ProjectRevision,
    is_active,
    is_deleted,
)

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


def _draft() -> ProjectDraft:
    return ProjectDraft(content={"screens": []})


def _revision(value: int = 0) -> ProjectRevision:
    return ProjectRevision(value=value)


def _project(**overrides: object) -> Project:
    defaults: dict[str, object] = {
        "id": "synthetic-project",
        "owner_id": "synthetic-owner",
        "name": "Synthetic Project",
        "created_at": NOW,
        "updated_at": NOW,
        "draft": _draft(),
        "schema_version": 14,
        "revision": _revision(0),
        "lifecycle_state": LIFECYCLE_ACTIVE,
        "project_type": PROJECT_TYPE_APP,
    }
    defaults.update(overrides)
    return Project(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ProjectType / constants
# ---------------------------------------------------------------------------


def test_supported_project_types() -> None:
    assert PROJECT_TYPE_APP in SUPPORTED_PROJECT_TYPES
    assert PROJECT_TYPE_WEBSITE in SUPPORTED_PROJECT_TYPES
    assert DEFAULT_PROJECT_TYPE == PROJECT_TYPE_APP


# ---------------------------------------------------------------------------
# ProjectRevision
# ---------------------------------------------------------------------------


def test_revision_accepts_nonnegative_int() -> None:
    r = ProjectRevision(value=5)
    assert r.value == 5


def test_revision_rejects_negative() -> None:
    with pytest.raises(ValueError):
        ProjectRevision(value=-1)


def test_revision_rejects_bool() -> None:
    with pytest.raises(ValueError):
        ProjectRevision(value=True)


def test_revision_rejects_non_int() -> None:
    with pytest.raises(ValueError):
        ProjectRevision(value="1")  # type: ignore[arg-type]


def test_revision_is_frozen() -> None:
    r = ProjectRevision(value=0)
    with pytest.raises(FrozenInstanceError):
        r.value = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProjectDraft
# ---------------------------------------------------------------------------


def test_draft_accepts_dict() -> None:
    d = ProjectDraft(content={"key": "value"})
    assert d.content == {"key": "value"}


def test_draft_rejects_non_dict() -> None:
    with pytest.raises(ValueError):
        ProjectDraft(content="not-a-dict")  # type: ignore[arg-type]


def test_draft_is_frozen() -> None:
    d = ProjectDraft(content={})
    with pytest.raises(FrozenInstanceError):
        d.content = {"x": 1}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


def test_project_defaults_to_app_type() -> None:
    p = _project(project_type=PROJECT_TYPE_APP)
    assert p.project_type == PROJECT_TYPE_APP


def test_project_accepts_website_type() -> None:
    p = _project(project_type=PROJECT_TYPE_WEBSITE)
    assert p.project_type == PROJECT_TYPE_WEBSITE


def test_project_rejects_invalid_type() -> None:
    with pytest.raises(ValueError):
        _project(project_type="mobile")


def test_project_rejects_invalid_lifecycle() -> None:
    with pytest.raises(ValueError):
        _project(lifecycle_state="archived")


def test_project_rejects_invalid_schema_version() -> None:
    with pytest.raises(ValueError):
        _project(schema_version=0)
    with pytest.raises(ValueError):
        _project(schema_version=True)


def test_project_rejects_empty_id() -> None:
    with pytest.raises(ValueError):
        _project(id="")


def test_project_rejects_empty_owner() -> None:
    with pytest.raises(ValueError):
        _project(owner_id="")


def test_project_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        _project(name="")


def test_project_rejects_naive_datetime() -> None:
    naive = datetime(2026, 1, 2, 3, 4)
    with pytest.raises(ValueError):
        _project(created_at=naive)


def test_project_rejects_invalid_draft() -> None:
    with pytest.raises(ValueError):
        _project(draft="not-a-draft")


def test_project_rejects_invalid_revision() -> None:
    with pytest.raises(ValueError):
        _project(revision="not-a-revision")


def test_project_is_frozen() -> None:
    p = _project()
    with pytest.raises(FrozenInstanceError):
        p.name = "changed"  # type: ignore[misc]


def test_is_active() -> None:
    p = _project(lifecycle_state=LIFECYCLE_ACTIVE)
    assert is_active(p) is True
    assert is_deleted(p) is False


def test_is_deleted() -> None:
    p = _project(lifecycle_state=LIFECYCLE_DELETED)
    assert is_deleted(p) is True
    assert is_active(p) is False


def test_legacy_project_defaults_to_app() -> None:
    # A project created without project_type explicitly defaults to 'app'
    p = Project(
        id="legacy",
        owner_id="owner",
        name="Legacy",
        created_at=NOW,
        updated_at=NOW,
        draft=_draft(),
        schema_version=13,
        revision=_revision(0),
        lifecycle_state=LIFECYCLE_ACTIVE,
    )
    assert p.project_type == DEFAULT_PROJECT_TYPE
    assert p.project_type == PROJECT_TYPE_APP
