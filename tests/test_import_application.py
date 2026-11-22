"""Unit tests for project import use cases."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from novy.application.import_project import (
    ImportPayload,
    ImportService,
)


def test_validate_payload_requires_project() -> None:
    service = ImportService(MagicMock())
    with pytest.raises(ValueError):
        service.validate_payload(ImportPayload(project="not-a-dict"))  # type: ignore[arg-type]


def test_validate_payload_requires_name() -> None:
    service = ImportService(MagicMock())
    with pytest.raises(ValueError):
        service.validate_payload(ImportPayload(project={}))


def test_validate_payload_rejects_empty_name() -> None:
    service = ImportService(MagicMock())
    with pytest.raises(ValueError):
        service.validate_payload(ImportPayload(project={"name": ""}))


def test_validate_payload_rejects_unsupported_schema() -> None:
    service = ImportService(MagicMock())
    with pytest.raises(ValueError):
        service.validate_payload(ImportPayload(project={"name": "Test", "schemaVersion": 99}))


def test_validate_payload_accepts_valid() -> None:
    service = ImportService(MagicMock())
    service.validate_payload(ImportPayload(project={"name": "Test", "schemaVersion": 14}))


def test_import_requires_idempotency_key() -> None:
    async def run() -> None:
        service = ImportService(MagicMock())
        await service.import_project("owner", ImportPayload(project={"name": "Test"}), "")

    with pytest.raises(ValueError):
        import asyncio

        asyncio.run(run())


def test_import_calls_store() -> None:
    import asyncio

    store = MagicMock()
    store.import_project = AsyncMock(return_value="new-project-id")
    service = ImportService(store)
    result = asyncio.run(
        service.import_project(
            "owner",
            ImportPayload(project={"name": "Test"}),
            "key-123",
        )
    )
    assert result.project_id == "new-project-id"
    assert result.name == "Test"
    assert result.idempotency_key == "key-123"
