"""Snapshot and hosted activation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from novy.application.snapshots import SnapshotService
from novy.presentation.http.auth_dependencies import get_session_credential
from novy.presentation.http.errors import ApiError, api_error
from novy.presentation.http.snapshot_schemas import (
    ActivateSnapshotRequest,
    HostedStatusResponse,
)

router = APIRouter(prefix="/projects", tags=["snapshots"])


def _snapshot_service(request: Request) -> SnapshotService:
    service = getattr(request.app.state, "snapshot_service", None)
    if not isinstance(service, SnapshotService):
        raise api_error(503, "snapshots_unavailable", "Snapshot service is unavailable")
    return service


def _owner_id(request: Request) -> str:
    credential = get_session_credential(request)
    if not credential:
        raise api_error(401, "unauthorized", "Authentication required")
    return credential


def _translate(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, ValueError):
        return api_error(400, "validation_error", str(exc))
    return api_error(500, "snapshot_error", "Snapshot operation failed")


@router.post("/{project_id}/activate", status_code=200)
async def activate_snapshot(
    project_id: str, payload: ActivateSnapshotRequest, request: Request
) -> HostedStatusResponse:
    service = _snapshot_service(request)
    _owner_id(request)
    environment = request.query_params.get("environment", "preview")
    try:
        await service.activate_snapshot(project_id, environment, payload.snapshotId)
    except Exception as exc:
        raise _translate(exc) from None
    return HostedStatusResponse(
        projectId=project_id,
        environment=environment,
        activeSnapshotId=payload.snapshotId,
        status="active",
    )


@router.get("/{project_id}/status", response_model=HostedStatusResponse)
async def hosted_status(project_id: str, request: Request) -> HostedStatusResponse:
    service = _snapshot_service(request)
    _owner_id(request)
    environment = request.query_params.get("environment", "preview")
    try:
        snapshot = await service.get_active_snapshot(project_id, environment)
    except Exception as exc:
        raise _translate(exc) from None
    if snapshot is None:
        return HostedStatusResponse(
            projectId=project_id,
            environment=environment,
            status="inactive",
        )
    return HostedStatusResponse(
        projectId=project_id,
        environment=environment,
        activeSnapshotId=snapshot.id,
        status="active",
    )
