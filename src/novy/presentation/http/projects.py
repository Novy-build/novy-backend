"""Project management endpoints.

Thin presentation layer: authenticate, validate transport shape, invoke use
case, map result or safe domain/application error to versioned response.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request, Response

from novy.application.import_project import (
    ImportPayload,
    ImportService,
)
from novy.application.projects import (
    CreateProjectInput,
    ProjectService,
    ProjectStoreError,
)
from novy.application.template_provisioning import (
    TemplateBlueprint,
    TemplateCollection,
    TemplateDatabase,
    TemplateProvisioningError,
    TemplateProvisioningService,
)
from novy.domain.projects import (
    ProjectConflictError,
    ProjectDataError,
    ProjectDraft,
    ProjectNotFoundError,
    ProjectPreconditionRequiredError,
)
from novy.presentation.http.auth_dependencies import get_session_credential
from novy.presentation.http.errors import ApiError, api_error
from novy.presentation.http.project_schemas import (
    CreateProjectRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectSummaryResponse,
    RenameProjectRequest,
    SaveDocumentRequest,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_service(request: Request) -> ProjectService:
    service = getattr(request.app.state, "project_service", None)
    if not isinstance(service, ProjectService):
        raise api_error(503, "projects_unavailable", "Project service is unavailable")
    return service


def _import_service(request: Request) -> ImportService:
    service = getattr(request.app.state, "import_service", None)
    if not isinstance(service, ImportService):
        raise api_error(503, "import_unavailable", "Import service is unavailable")
    return service


def _template_provisioning_service(request: Request) -> TemplateProvisioningService:
    service = getattr(request.app.state, "template_provisioning_service", None)
    if not isinstance(service, TemplateProvisioningService):
        raise api_error(503, "projects_unavailable", "Project service is unavailable")
    return service


def _template_blueprint(template: dict[str, object], name: str) -> TemplateBlueprint:
    """Translate the frontend template payload into a provisioning blueprint."""
    screens = template.get("screens")
    screen_list = (
        [dict(screen) for screen in screens if isinstance(screen, dict)]
        if isinstance(screens, list)
        else []
    )
    databases: list[TemplateDatabase] = []
    data = template.get("data")
    if isinstance(data, dict):
        raw_collections = data.get("collections")
        collections: list[TemplateCollection] = []
        if isinstance(raw_collections, list):
            for raw in raw_collections:
                if not isinstance(raw, dict):
                    continue
                raw_fields = raw.get("fields")
                raw_records = raw.get("records")
                identifier = raw.get("identifier")
                collections.append(
                    TemplateCollection(
                        name=str(raw.get("name", "")),
                        reference_id=str(raw.get("referenceId", "")),
                        fields=(
                            [dict(field) for field in raw_fields if isinstance(field, dict)]
                            if isinstance(raw_fields, list)
                            else []
                        ),
                        records=(
                            [dict(record) for record in raw_records if isinstance(record, dict)]
                            if isinstance(raw_records, list)
                            else []
                        ),
                        identifier=dict(identifier) if isinstance(identifier, dict) else None,
                    )
                )
        db_name = data.get("databaseName")
        databases.append(
            TemplateDatabase(
                name=(
                    str(db_name)
                    if isinstance(db_name, str) and db_name.strip()
                    else "Project database"
                ),
                collections=collections,
            )
        )
    project_type = template.get("projectType")
    return TemplateBlueprint(
        name=name,
        project_type=(
            project_type
            if isinstance(project_type, str) and project_type in {"app", "website"}
            else "app"
        ),
        screens=screen_list,
        databases=databases,
    )


def _owner_id(request: Request) -> str:
    # In production this comes from the authenticated builder session.
    # For now, derive from a header or session credential.
    credential = get_session_credential(request)
    if not credential:
        raise api_error(401, "unauthorized", "Authentication required")
    return credential


def _translate(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, ProjectNotFoundError):
        return api_error(404, "not_found", "Project not found")
    if isinstance(exc, ProjectConflictError):
        return api_error(409, "stale_revision", "Project revision conflict")
    if isinstance(exc, ProjectPreconditionRequiredError):
        return api_error(428, "precondition_required", "Missing revision precondition")
    if isinstance(exc, ProjectDataError):
        return api_error(400, "validation_error", "Invalid project data")
    if isinstance(exc, ProjectStoreError):
        return api_error(503, "store_unavailable", "Project storage is unavailable")
    if isinstance(exc, TemplateProvisioningError):
        return api_error(400, "validation_error", "Invalid template blueprint")
    if isinstance(exc, ValueError):
        return api_error(400, "validation_error", "Invalid input")
    return api_error(500, "project_error", "Project operation failed")


def _translate_import(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    from novy.application.import_project import ImportConflictError, ImportStoreError

    if isinstance(exc, ImportConflictError):
        return api_error(409, "import_conflict", "Import idempotency key conflict")
    if isinstance(exc, ImportStoreError):
        return api_error(503, "import_store_unavailable", "Import storage is unavailable")
    if isinstance(exc, ValueError):
        return api_error(400, "validation_error", str(exc))
    return api_error(500, "import_error", "Import failed")


@router.get("", response_model=ProjectListResponse)
async def list_projects(request: Request) -> ProjectListResponse:
    service = _project_service(request)
    owner_id = _owner_id(request)
    cursor = request.query_params.get("cursor")
    limit = int(request.query_params.get("limit", "20"))
    try:
        projects = await service.list_projects(owner_id, cursor=cursor, limit=limit)
    except Exception as exc:
        raise _translate(exc) from None
    summaries = []
    for p in projects:
        screens = p.draft.get("screens", [])
        screen_count = len(screens) if isinstance(screens, list) else 0
        summaries.append(
            ProjectSummaryResponse(
                id=p.id,
                name=p.name,
                projectType=p.project_type,
                createdAt=p.created_at,
                updatedAt=p.updated_at,
                screenCount=screen_count,
            )
        )
    return ProjectListResponse(projects=summaries)


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(payload: CreateProjectRequest, request: Request) -> ProjectResponse:
    service = _project_service(request)
    owner_id = _owner_id(request)
    if payload.template is not None:
        provisioning = _template_provisioning_service(request)
        blueprint = _template_blueprint(payload.template, payload.name)
        try:
            project_id = await provisioning.provision(owner_id, blueprint)
            result = await service.get_project(project_id, owner_id)
        except Exception as exc:
            raise _translate(exc) from None
        return _to_response(result)

    draft_content: dict[str, object] = {}
    project_type = "app"
    try:
        result = await service.create_project(
            CreateProjectInput(
                owner_id=owner_id,
                name=payload.name,
                draft=ProjectDraft(content=draft_content),
                project_type=project_type,
            )
        )
    except Exception as exc:
        raise _translate(exc) from None
    return _to_response(result)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, request: Request) -> ProjectResponse:
    service = _project_service(request)
    owner_id = _owner_id(request)
    try:
        result = await service.get_project(project_id, owner_id)
    except Exception as exc:
        raise _translate(exc) from None
    return _to_response(result)


@router.put("/{project_id}/document", response_model=ProjectResponse)
async def save_document(
    project_id: str,
    payload: SaveDocumentRequest,
    request: Request,
    if_match: str | None = Header(None, alias="If-Match"),
) -> ProjectResponse:
    service = _project_service(request)
    owner_id = _owner_id(request)
    if if_match is None:
        raise api_error(428, "precondition_required", "Missing If-Match header")
    # Parse revision from quoted If-Match
    expected_revision = _parse_revision(if_match)
    new_draft = ProjectDraft(
        content={"schemaVersion": payload.schemaVersion, "screens": payload.screens}
    )
    try:
        result = await service.save_document(project_id, owner_id, expected_revision, new_draft)
    except Exception as exc:
        raise _translate(exc) from None
    return _to_response(result)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def rename_project(
    project_id: str, payload: RenameProjectRequest, request: Request
) -> ProjectResponse:
    service = _project_service(request)
    owner_id = _owner_id(request)
    try:
        result = await service.rename_project(project_id, owner_id, payload.name)
    except Exception as exc:
        raise _translate(exc) from None
    return _to_response(result)


@router.post("/{project_id}/duplicate", response_model=ProjectResponse, status_code=201)
async def duplicate_project(
    project_id: str, request: Request, copy_records: bool = False
) -> ProjectResponse:
    service = _project_service(request)
    owner_id = _owner_id(request)
    try:
        result = await service.duplicate_project(project_id, owner_id, copy_records=copy_records)
    except Exception as exc:
        raise _translate(exc) from None
    return _to_response(result)


@router.post("/import", response_model=ProjectResponse, status_code=201)
async def import_project(request: Request) -> ProjectResponse:
    service = _import_service(request)
    owner_id = _owner_id(request)
    body = await request.json()
    idempotency_key = request.headers.get("X-Idempotency-Key", "")
    payload = ImportPayload(
        project=body.get("project", {}),
        app_data=body.get("appData", {}),
    )
    try:
        result = await service.import_project(owner_id, payload, idempotency_key)
    except Exception as exc:
        raise _translate_import(exc) from None
    # Fetch the imported project
    project_service = _project_service(request)
    try:
        view = await project_service.get_project(result.project_id, owner_id)
    except Exception as exc:
        raise _translate(exc) from None
    return _to_response(view)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request) -> Response:
    service = _project_service(request)
    owner_id = _owner_id(request)
    try:
        await service.delete_project(project_id, owner_id)
    except Exception as exc:
        raise _translate(exc) from None
    return Response(status_code=204)


def _to_response(view: Any) -> ProjectResponse:
    draft = view.draft
    content = draft if isinstance(draft, dict) else getattr(draft, "content", {})
    screens = content.get("screens", []) if isinstance(content, dict) else []
    return ProjectResponse(
        id=view.id,
        name=view.name,
        projectType=view.project_type,
        createdAt=view.created_at,
        updatedAt=view.updated_at,
        schemaVersion=view.schema_version,
        revision=view.revision,
        screens=screens if isinstance(screens, list) else [],
        lifecycleState=view.lifecycle_state,
        draft=view.draft,
    )


def _parse_revision(if_match: str) -> int:
    value = if_match.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    try:
        return int(value)
    except ValueError:
        raise api_error(428, "precondition_required", "Invalid If-Match revision") from None
