"""Builder authentication endpoints.

These routes exchange verified Supabase identities for opaque Novy sessions.
Provider tokens never appear in HTTP responses.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from novy.application.builder_auth import (
    AuthResult,
    BuilderAuthError,
    BuilderAuthService,
    RegistrationResult,
)
from novy.application.builder_profiles import BuilderProfileStoreError
from novy.application.builder_sessions import IssuedBuilderSession
from novy.domain.builder import BuilderAccessDenied
from novy.domain.builder_sessions import (
    BuilderSessionConflict,
    BuilderSessionInvalid,
    BuilderSessionStoreError,
    SessionCredentialInvalid,
)
from novy.infrastructure.supabase_auth import SupabaseAuthError
from novy.infrastructure.supabase_jwt import SupabaseTokenError
from novy.presentation.http.auth_dependencies import (
    get_session_credential,
    require_unsafe_request_guards,
)
from novy.presentation.http.auth_schemas import (
    CsrfBootstrapResponse,
    LoginRequest,
    RegisterRequest,
    RegistrationPendingResponse,
    UserResponse,
)
from novy.presentation.http.builder_session_cookie import (
    clear_auth_cookies,
    set_builder_session_cookie,
    set_csrf_cookie,
    set_preauth_cookie,
)
from novy.presentation.http.errors import ApiError, api_error

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/csrf", response_model=CsrfBootstrapResponse)
async def csrf_bootstrap(request: Request, response: Response) -> CsrfBootstrapResponse:
    service = getattr(request.app.state, "csrf_service", None)
    if service is None:
        raise api_error(503, "auth_unavailable", "Authentication is unavailable")
    preauth = service.issue_preauth()
    response.headers["Cache-Control"] = "no-store"
    _set_preauth_cookies(response, preauth.token, preauth.state)
    return CsrfBootstrapResponse(csrfToken=preauth.token)


@router.post(
    "/register",
    responses={
        200: {"model": UserResponse},
        202: {"model": RegistrationPendingResponse},
    },
    dependencies=[Depends(require_unsafe_request_guards)],
)
async def register(payload: RegisterRequest, request: Request, response: Response) -> object:
    service = _auth_service(request)
    try:
        result = await service.register(
            email=payload.email, password=payload.password, display_name=payload.display_name
        )
    except Exception as exc:  # translated below; never expose raw provider details
        raise _translate(exc) from None
    return _registration_response(
        result, response, getattr(request.app.state, "csrf_service", None)
    )


@router.post(
    "/login",
    response_model=UserResponse,
    dependencies=[Depends(require_unsafe_request_guards)],
)
async def login(payload: LoginRequest, request: Request, response: Response) -> UserResponse:
    service = _auth_service(request)
    try:
        result = await service.login(email=payload.email, password=payload.password)
    except Exception as exc:
        raise _translate(exc) from None
    _set_session_cookies(response, result, getattr(request.app.state, "csrf_service", None))
    return UserResponse(
        id=result.user.id, displayName=result.user.display_name, email=result.user.email
    )


@router.get("/session", response_model=UserResponse)
async def session(request: Request, response: Response) -> UserResponse:
    service = _auth_service(request)
    credential = get_session_credential(request)
    try:
        user = await service.session(credential)
    except Exception as exc:
        raise _translate(exc) from None
    csrf = getattr(request.app.state, "csrf_service", None)
    if csrf is not None:
        set_csrf_cookie(response, csrf.token_for_session(credential))
    return UserResponse(id=user.id, displayName=user.display_name, email=user.email)


@router.post(
    "/refresh",
    response_model=UserResponse,
    dependencies=[Depends(require_unsafe_request_guards)],
)
async def refresh(request: Request, response: Response) -> UserResponse:
    service = _auth_service(request)
    credential = get_session_credential(request)
    try:
        result = await service.refresh(credential)
    except Exception as exc:
        raise _translate(exc) from None
    _set_session_cookies(response, result, getattr(request.app.state, "csrf_service", None))
    return UserResponse(
        id=result.user.id, displayName=result.user.display_name, email=result.user.email
    )


@router.post("/logout", status_code=204, dependencies=[Depends(require_unsafe_request_guards)])
async def logout(request: Request, response: Response) -> Response:
    service = _auth_service(request)
    credential = get_session_credential(request)
    try:
        await service.logout(credential)
    except Exception as exc:
        raise _translate(exc) from None
    clear_auth_cookies(response)
    response.status_code = 204
    response.headers["Cache-Control"] = "no-store"
    return response


def _auth_service(request: Request) -> BuilderAuthService:
    service = request.app.state.builder_auth_service
    if not isinstance(service, BuilderAuthService):
        raise api_error(503, "auth_unavailable", "Authentication is unavailable")
    return service


def _set_preauth_cookies(response: Response, csrf_token: str, state: str) -> None:
    set_csrf_cookie(response, csrf_token)
    set_preauth_cookie(response, state)


def _set_session_cookies(response: Response, result: AuthResult, csrf_service: Any) -> None:
    issued = IssuedBuilderSession(credential=result.credential, record_id=result.record_id)
    set_builder_session_cookie(response, issued)
    if csrf_service is not None:
        set_csrf_cookie(response, csrf_service.token_for_session(result.credential))


def _registration_response(
    result: RegistrationResult, response: Response, csrf_service: Any
) -> Response | UserResponse:
    if result.requires_confirmation:
        payload = RegistrationPendingResponse(
            user=UserResponse(
                id=result.user.id,
                displayName=result.user.display_name,
                email=result.user.email,
            ),
            requiresEmailConfirmation=True,
        )
        json_response = JSONResponse(
            status_code=202,
            content=payload.model_dump(by_alias=True),
            headers={"Cache-Control": "no-store"},
        )
        return json_response
    if result.credential is None:
        raise api_error(500, "auth_failed", "Authentication did not produce a session")
    issued = AuthResult(
        user=result.user, credential=result.credential, record_id=result.record_id or ""
    )
    _set_session_cookies(response, issued, csrf_service)
    return UserResponse(
        id=result.user.id, displayName=result.user.display_name, email=result.user.email
    )


def _translate(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, SupabaseAuthError):
        return api_error(exc.status_code, exc.code, "Authentication failed")
    if isinstance(exc, SupabaseTokenError):
        return api_error(401, "invalid_session", "The identity token is invalid")
    if isinstance(exc, BuilderAuthError):
        return api_error(exc.status_code, exc.code, "Authentication failed")
    if isinstance(exc, (BuilderAccessDenied,)):
        return api_error(403, "builder_disabled", "This builder account is not active")
    if isinstance(exc, (SessionCredentialInvalid, BuilderSessionInvalid)):
        return api_error(401, "invalid_session", "The session is invalid")
    if isinstance(exc, BuilderSessionConflict):
        return api_error(409, "session_conflict", "The session changed; try again")
    if isinstance(exc, (BuilderSessionStoreError, BuilderProfileStoreError)):
        return api_error(503, "auth_store_unavailable", "Authentication storage is unavailable")
    if isinstance(exc, ValueError):
        return api_error(400, "validation_error", "Invalid authentication input")
    return api_error(500, "auth_failed", "Authentication failed")
