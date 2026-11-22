"""Unit tests for the project application-layer port."""

from novy.application.projects import ProjectRepository, ProjectStoreError


def test_protocol_is_runtime_checkable_shape() -> None:
    # Verify the protocol defines the expected methods.
    expected = {
        "create",
        "find_by_id_and_owner",
        "find_by_owner",
        "save_with_revision",
        "rename",
        "delete",
        "exists",
    }
    actual = {
        name
        for name in dir(ProjectRepository)
        if not name.startswith("_") and callable(getattr(ProjectRepository, name, None))
    }
    assert expected.issubset(actual)


def test_store_error_is_exception() -> None:
    err = ProjectStoreError()
    assert isinstance(err, Exception)
    assert "storage" in str(err).lower() or "unavailable" in str(err).lower()
