"""Project HTTP responses must match the frontend project response contract."""

from novy.application.projects import ProjectView
from novy.presentation.http.projects import _to_response


def test_project_response_exposes_top_level_screens() -> None:
    view = ProjectView(
        id="project_1",
        owner_id="builder_1",
        name="Notes",
        created_at="2026-11-22T00:00:00+00:00",
        updated_at="2026-11-22T00:00:00+00:00",
        schema_version=14,
        revision=1,
        lifecycle_state="active",
        project_type="app",
        draft={"screens": [{"id": "screen_home", "nodes": []}]},
    )

    response = _to_response(view)

    assert response.screens == [{"id": "screen_home", "nodes": []}]
    assert response.model_dump(by_alias=True)["screens"] == [{"id": "screen_home", "nodes": []}]


def test_project_response_defaults_to_empty_screens() -> None:
    view = ProjectView(
        id="project_2",
        owner_id="builder_1",
        name="Blank",
        created_at="2026-11-22T00:00:00+00:00",
        updated_at="2026-11-22T00:00:00+00:00",
        schema_version=14,
        revision=0,
        lifecycle_state="active",
        project_type="app",
        draft={},
    )

    assert _to_response(view).screens == []
