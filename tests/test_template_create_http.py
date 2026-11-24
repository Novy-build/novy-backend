"""The template create route provisions data instead of copying a dangling draft."""

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from novy.application.projects import ProjectService
from novy.application.template_provisioning import TemplateProvisioningService
from novy.infrastructure.settings import Settings

CREDENTIAL = "a" * 43
NOW = datetime(2026, 11, 25, tzinfo=UTC)


class FakeView:
    id = "project_1"
    name = "Notes"
    project_type = "app"
    created_at = NOW.isoformat()
    updated_at = NOW.isoformat()
    schema_version = 14
    revision = 0
    lifecycle_state = "active"
    draft: dict[str, Any] = {"screens": []}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[TestClient, MagicMock], None, None]:
    import novy.presentation.http.projects as project_routes
    from novy.main import create_app

    monkeypatch.setattr(project_routes, "get_session_credential", lambda request: CREDENTIAL)
    app = create_app(Settings(environment="test"))
    with TestClient(app) as test_client:
        provisioning = MagicMock(spec=TemplateProvisioningService)
        provisioning.provision = AsyncMock(return_value="project_1")
        projects = MagicMock(spec=ProjectService)
        projects.get_project = AsyncMock(return_value=FakeView())
        app.state.project_service = projects
        app.state.template_provisioning_service = provisioning
        yield test_client, provisioning


def test_template_create_passes_data_blueprint_to_provisioning(
    client: tuple[TestClient, MagicMock],
) -> None:
    test_client, provisioning = client

    response = test_client.post(
        "/api/v1/projects",
        json={
            "name": "Notes",
            "template": {
                "projectType": "app",
                "screens": [
                    {
                        "id": "screen_home",
                        "logicGraphs": [
                            {
                                "nodes": [
                                    {
                                        "payload": {
                                            "type": "load-list-items",
                                            "collectionId": "template-notes-collection",
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "data": {
                    "databaseName": "Notes database",
                    "collections": [
                        {
                            "referenceId": "template-notes-collection",
                            "name": "Notes",
                            "fields": [
                                {
                                    "id": "note_title",
                                    "key": "title",
                                    "label": "Title",
                                    "type": "text",
                                    "required": True,
                                }
                            ],
                            "identifier": {
                                "key": "noteId",
                                "type": "string",
                                "generation": "backend",
                            },
                            "records": [],
                        }
                    ],
                },
            },
        },
    )

    assert response.status_code == 201
    blueprint = provisioning.provision.await_args.args[1]
    assert blueprint.databases[0].collections[0].reference_id == "template-notes-collection"
    assert blueprint.databases[0].collections[0].identifier == {
        "key": "noteId",
        "type": "string",
        "generation": "backend",
    }
    assert blueprint.screens[0]["logicGraphs"][0]["nodes"][0]["payload"]["collectionId"] == (
        "template-notes-collection"
    )
