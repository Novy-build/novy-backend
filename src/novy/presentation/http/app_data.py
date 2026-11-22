"""HTTP routes for project-scoped databases, collections, and records."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response

from novy.application.app_data import AppDataStoreError
from novy.composition import AppDataRepositories
from novy.domain.app_data import AppCollection, AppDatabase, AppRecord
from novy.domain.projects import ProjectNotFoundError
from novy.presentation.http.app_data_schemas import (
    AppDataSnapshotResponse,
    CreateCollectionRequest,
    CreateDatabaseRequest,
    CreateRecordRequest,
    DataCollectionResponse,
    DataFieldResponse,
    DataRecordResponse,
    ProjectDatabaseResponse,
    RecordIdentifierResponse,
    UpdateCollectionRequest,
    UpdateDatabaseRequest,
    UpdateRecordRequest,
)
from novy.presentation.http.auth_dependencies import get_session_credential
from novy.presentation.http.errors import ApiError, api_error

router = APIRouter(prefix="/projects", tags=["app-data"])

_PREVIEW_ENVIRONMENT = "preview"


def _repositories(request: Request) -> AppDataRepositories:
    repositories = getattr(request.app.state, "app_data_repositories", None)
    if not isinstance(repositories, AppDataRepositories):
        raise api_error(503, "app_data_unavailable", "Application data is unavailable")
    return repositories


async def _authorize_project(request: Request, project_id: str) -> str:
    owner_id = get_session_credential(request)
    service = getattr(request.app.state, "project_service", None)
    if service is None:
        raise api_error(503, "projects_unavailable", "Project service is unavailable")
    try:
        await service.get_project(project_id, owner_id)
    except ProjectNotFoundError:
        raise api_error(404, "not_found", "Project not found") from None
    except Exception as exc:
        raise _translate(exc) from None
    return owner_id


def _translate(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, AppDataStoreError):
        return api_error(
            503, "app_data_store_unavailable", "Application data storage is unavailable"
        )
    if isinstance(exc, ValueError):
        return api_error(400, "validation_error", "Invalid application data")
    return api_error(500, "app_data_error", "Application data operation failed")


def _database_response(database: AppDatabase) -> ProjectDatabaseResponse:
    return ProjectDatabaseResponse(
        id=database.id,
        projectId=database.project_id,
        name=database.name,
        createdAt=database.created_at.isoformat(),
        updatedAt=database.updated_at.isoformat(),
    )


def _collection_response(collection: AppCollection) -> DataCollectionResponse:
    identifier = None
    if isinstance(collection.record_identifier, str) and collection.record_identifier:
        identifier = RecordIdentifierResponse(key=collection.record_identifier)
    return DataCollectionResponse(
        id=collection.id,
        projectId=collection.project_id,
        databaseId=collection.database_id,
        name=collection.name,
        fields=[DataFieldResponse(**field) for field in collection.fields],
        identifier=identifier,
        createdAt=collection.created_at.isoformat(),
        updatedAt=collection.updated_at.isoformat(),
    )


def _record_response(record: AppRecord) -> DataRecordResponse:
    return DataRecordResponse(
        id=record.id,
        projectId=record.project_id,
        collectionId=record.collection_id,
        values=record.values,
        createdAt=record.created_at.isoformat(),
        updatedAt=record.updated_at.isoformat(),
    )


@router.get("/{project_id}/app-data", response_model=AppDataSnapshotResponse)
async def get_app_data_snapshot(project_id: str, request: Request) -> AppDataSnapshotResponse:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        databases = await repositories.databases.find_by_project(project_id)
        collections = await repositories.collections.find_by_project(project_id)
        records_by_collection: dict[str, list[DataRecordResponse]] = {}
        for collection in collections:
            records = await repositories.records.find_by_scope(
                project_id, collection.id, _PREVIEW_ENVIRONMENT
            )
            records_by_collection[collection.id] = [_record_response(record) for record in records]
    except Exception as exc:
        raise _translate(exc) from None
    return AppDataSnapshotResponse(
        databases=[_database_response(database) for database in databases],
        collections=[_collection_response(collection) for collection in collections],
        recordsByCollection=records_by_collection,
    )


@router.get("/{project_id}/databases", response_model=list[ProjectDatabaseResponse])
async def list_databases(project_id: str, request: Request) -> list[ProjectDatabaseResponse]:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        databases = await repositories.databases.find_by_project(project_id)
    except Exception as exc:
        raise _translate(exc) from None
    return [_database_response(database) for database in databases]


@router.post("/{project_id}/databases", response_model=ProjectDatabaseResponse, status_code=201)
async def create_database(
    project_id: str, payload: CreateDatabaseRequest, request: Request
) -> ProjectDatabaseResponse:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        database = await repositories.databases.create(project_id, payload.name)
    except Exception as exc:
        raise _translate(exc) from None
    return _database_response(database)


@router.patch("/{project_id}/databases/{database_id}", response_model=ProjectDatabaseResponse)
async def update_database(
    project_id: str, database_id: str, payload: UpdateDatabaseRequest, request: Request
) -> ProjectDatabaseResponse:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        database = await repositories.databases.update(database_id, project_id, payload.name)
    except Exception as exc:
        raise _translate(exc) from None
    return _database_response(database)


@router.delete("/{project_id}/databases/{database_id}", status_code=204)
async def delete_database(project_id: str, database_id: str, request: Request) -> Response:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        await repositories.databases.delete(database_id, project_id)
    except Exception as exc:
        raise _translate(exc) from None
    return Response(status_code=204)


@router.get("/{project_id}/collections", response_model=list[DataCollectionResponse])
async def list_collections(
    project_id: str,
    request: Request,
    databaseId: str | None = Query(default=None),
) -> list[DataCollectionResponse]:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        collections = await repositories.collections.find_by_project(project_id, databaseId)
    except Exception as exc:
        raise _translate(exc) from None
    return [_collection_response(collection) for collection in collections]


@router.post("/{project_id}/collections", response_model=DataCollectionResponse, status_code=201)
async def create_collection(
    project_id: str, payload: CreateCollectionRequest, request: Request
) -> DataCollectionResponse:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    database_id = payload.databaseId
    if not database_id:
        databases = await repositories.databases.find_by_project(project_id)
        if not databases:
            raise api_error(400, "validation_error", "A database is required")
        database_id = databases[0].id
    identifier = payload.identifier.key if payload.identifier is not None else None
    try:
        collection = await repositories.collections.create(
            project_id,
            database_id,
            payload.name,
            [field.model_dump() for field in payload.fields],
            identifier,
        )
    except Exception as exc:
        raise _translate(exc) from None
    return _collection_response(collection)


@router.get("/{project_id}/collections/{collection_id}", response_model=DataCollectionResponse)
async def get_collection(
    project_id: str, collection_id: str, request: Request
) -> DataCollectionResponse:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        collection = await repositories.collections.find_by_id_and_project(
            collection_id, project_id
        )
    except Exception as exc:
        raise _translate(exc) from None
    if collection is None:
        raise api_error(404, "not_found", "Collection not found")
    return _collection_response(collection)


@router.put("/{project_id}/collections/{collection_id}", response_model=DataCollectionResponse)
async def update_collection(
    project_id: str, collection_id: str, payload: UpdateCollectionRequest, request: Request
) -> DataCollectionResponse:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    identifier = payload.identifier.key if payload.identifier is not None else None
    try:
        collection = await repositories.collections.update(
            collection_id,
            project_id,
            name=payload.name,
            fields=[field.model_dump() for field in payload.fields],
            record_identifier=identifier,
        )
    except Exception as exc:
        raise _translate(exc) from None
    return _collection_response(collection)


@router.delete("/{project_id}/collections/{collection_id}", status_code=204)
async def delete_collection(project_id: str, collection_id: str, request: Request) -> Response:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        await repositories.collections.delete(collection_id, project_id)
    except Exception as exc:
        raise _translate(exc) from None
    return Response(status_code=204)


@router.get(
    "/{project_id}/collections/{collection_id}/records",
    response_model=list[DataRecordResponse],
)
async def list_records(
    project_id: str, collection_id: str, request: Request
) -> list[DataRecordResponse]:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        records = await repositories.records.find_by_scope(
            project_id, collection_id, _PREVIEW_ENVIRONMENT
        )
    except Exception as exc:
        raise _translate(exc) from None
    return [_record_response(record) for record in records]


@router.post(
    "/{project_id}/collections/{collection_id}/records",
    response_model=DataRecordResponse,
    status_code=201,
)
async def create_record(
    project_id: str, collection_id: str, payload: CreateRecordRequest, request: Request
) -> DataRecordResponse:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        record = await repositories.records.create(
            project_id, collection_id, _PREVIEW_ENVIRONMENT, payload.values
        )
    except Exception as exc:
        raise _translate(exc) from None
    return _record_response(record)


@router.put(
    "/{project_id}/collections/{collection_id}/records/{record_id}",
    response_model=DataRecordResponse,
)
async def update_record(
    project_id: str,
    collection_id: str,
    record_id: str,
    payload: UpdateRecordRequest,
    request: Request,
) -> DataRecordResponse:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        record = await repositories.records.update(
            record_id, project_id, collection_id, _PREVIEW_ENVIRONMENT, payload.values
        )
    except Exception as exc:
        raise _translate(exc) from None
    return _record_response(record)


@router.delete(
    "/{project_id}/collections/{collection_id}/records/{record_id}",
    status_code=204,
)
async def delete_record(
    project_id: str, collection_id: str, record_id: str, request: Request
) -> Response:
    await _authorize_project(request, project_id)
    repositories = _repositories(request)
    try:
        await repositories.records.delete(
            record_id, project_id, collection_id, _PREVIEW_ENVIRONMENT
        )
    except Exception as exc:
        raise _translate(exc) from None
    return Response(status_code=204)
