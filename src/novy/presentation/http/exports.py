"""React source export endpoint.

Thin presentation layer: guard the unsafe request, validate the transport shape,
invoke the use case and map safe errors to the frontend's error contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from novy.application.react_export import (
    ExportNotFoundError,
    ExportRateLimitError,
    ExportTimeoutError,
    ExportUnavailableError,
    ReactExportService,
)
from novy.domain.generation import GenerationError, InvalidManifestError, UnsupportedCapabilityError
from novy.presentation.http.auth_dependencies import (
    get_session_credential,
    require_unsafe_request_guards,
)
from novy.presentation.http.errors import ApiError, _response, api_error
from novy.presentation.http.export_schemas import (
    MAX_EXPORT_BODY_BYTES,
    GeneratedAppManifestRequest,
)

router = APIRouter(prefix="/exports", tags=["exports"])


class ExportBodyLimitMiddleware:
    """Reject oversized export bodies before transport validation runs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").endswith("/exports/react"):
            for name, value in scope.get("headers", []):
                if name == b"content-length":
                    try:
                        length = int(value)
                    except ValueError:
                        length = 0
                    if length > MAX_EXPORT_BODY_BYTES:
                        response = _response(
                            413, "payload_too_large", "Request payload too large", None
                        )
                        await response(scope, receive, send)
                        return
        await self.app(scope, receive, send)


def _service(request: Request) -> ReactExportService:
    service = getattr(request.app.state, "react_export_service", None)
    if not isinstance(service, ReactExportService):
        raise api_error(503, "auth_provider_unavailable", "Export service is unavailable")
    return service


async def export_guards(request: Request) -> None:
    """Authenticate, enforce CSRF/origin guards and bound the request body."""
    await require_unsafe_request_guards(request)
    get_session_credential(request)
    content_length = request.headers.get("content-length")
    if (
        content_length is not None
        and content_length.isdigit()
        and int(content_length) > MAX_EXPORT_BODY_BYTES
    ):
        raise api_error(413, "payload_too_large", "Request payload too large")
    body = await request.body()
    if len(body) > MAX_EXPORT_BODY_BYTES:
        raise api_error(413, "payload_too_large", "Request payload too large")


def _translate(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, InvalidManifestError):
        issues = [
            {"field": issue.location, "location": issue.location, "message": issue.message}
            for issue in exc.issues
        ]
        message = exc.issues[0].message if exc.issues else "Invalid export manifest"
        return api_error(400, "invalid_manifest", message, details={"issues": issues})
    if isinstance(exc, UnsupportedCapabilityError):
        issues = [
            {
                "field": capability,
                "location": "runtime.requirements",
                "message": f"Capability is not supported by the generator: {capability}",
            }
            for capability in exc.capabilities
        ]
        return api_error(
            400,
            "validation_error",
            issues[0]["message"],
            details={"issues": issues},
        )
    if isinstance(exc, ExportNotFoundError):
        return api_error(404, "not_found", "Project not found")
    if isinstance(exc, ExportUnavailableError):
        return api_error(503, "auth_provider_unavailable", "Authentication is unavailable")
    if isinstance(exc, ExportRateLimitError):
        return api_error(429, "rate_limited", "Too many export requests")
    if isinstance(exc, ExportTimeoutError):
        return api_error(504, "generation_timeout", "React generation timed out")
    if isinstance(exc, GenerationError):
        return api_error(500, "generation_failed", "React generation failed")
    return api_error(500, "generation_failed", "React generation failed")


@router.post("/react", dependencies=[Depends(export_guards)])
async def export_react(payload: GeneratedAppManifestRequest, request: Request) -> Response:
    credential = get_session_credential(request)
    service = _service(request)
    try:
        result = await service.export(payload.model_dump(), credential)
    except Exception as exc:
        raise _translate(exc) from None
    return Response(
        content=result.content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "Cache-Control": "no-store",
        },
    )
