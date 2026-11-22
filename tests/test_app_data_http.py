"""App-data HTTP routes expose the frontend project-data contract."""

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novy.composition import AppDataRepositories
from novy.domain.app_data import AppCollection, AppDatabase, AppRecord
from novy.infrastructure.settings import Settings

NOW = datetime(2026, 11, 22, tzinfo=UTC)
CREDENTIAL = "a" * 43


class FakeDatabases:
    async def find_by_project(self, project_id: str) -> list[AppDatabase]:
        return [
            AppDatabase(
                id="db_1",
                project_id=project_id,
                name="Data",
                created_at=NOW,
                updated_at=NOW,
            )
        ]


class FakeCollections:
    async def find_by_project(
        self, project_id: str, database_id: str | None = None
    ) -> list[AppCollection]:
        return [
            AppCollection(
                id="collection_1",
                project_id=project_id,
                database_id="db_1",
                name="Notes",
                fields=[{"id": "field_title", "key": "title", "label": "Title", "type": "text"}],
                record_identifier="noteId",
                created_at=NOW,
                updated_at=NOW,
            )
        ]


class FakeRecords:
    async def find_by_scope(
        self, project_id: str, collection_id: str, environment: str
    ) -> list[AppRecord]:
        return [
            AppRecord(
                id="record_1",
                project_id=project_id,
                collection_id=collection_id,
                environment=environment,
                values={"field_title": "Hello"},
                created_at=NOW,
                updated_at=NOW,
            )
        ]


class FakeProjectService:
    async def get_project(self, project_id: str, owner_id: str) -> object:
        return object()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    import novy.presentation.http.app_data as app_data_routes
    from novy.main import create_app

    monkeypatch.setattr(app_data_routes, "get_session_credential", lambda request: CREDENTIAL)
    app = create_app(Settings(environment="test"))
    with TestClient(app) as test_client:
        app.state.project_service = FakeProjectService()
        app.state.app_data_repositories = AppDataRepositories(
            databases=FakeDatabases(),  # type: ignore[arg-type]
            collections=FakeCollections(),  # type: ignore[arg-type]
            records=FakeRecords(),  # type: ignore[arg-type]
        )
        yield test_client


def test_app_data_snapshot_matches_frontend_contract(client: TestClient) -> None:
    response = client.get("/api/v1/projects/project_1/app-data")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["databases"][0]["id"] == "db_1"
    assert body["collections"][0]["identifier"] == {
        "key": "noteId",
        "type": "string",
        "generation": "backend",
    }
    assert body["recordsByCollection"]["collection_1"][0]["values"] == {"field_title": "Hello"}


def test_app_data_routes_require_a_session() -> None:
    from novy.main import create_app

    app = create_app(Settings(environment="test"))
    with TestClient(app) as client:
        assert client.get("/api/v1/projects/project_1/app-data").status_code == 401
