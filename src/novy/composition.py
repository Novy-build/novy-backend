from typing import Any

import httpx2
from pymongo.asynchronous.database import AsyncDatabase

from novy.application.builder_auth import BuilderAuthService
from novy.application.builder_sessions import BuilderSessionService
from novy.application.projects import ProjectService
from novy.infrastructure.builder_profiles import (
    BUILDER_PROFILES_COLLECTION,
    MongoBuilderProfileRepository,
)
from novy.infrastructure.builder_sessions import (
    BUILDER_SESSIONS_COLLECTION,
    MongoBuilderSessionRepository,
)
from novy.infrastructure.csrf import CsrfService
from novy.infrastructure.projects import (
    PROJECTS_COLLECTION,
    MongoProjectRepository,
)
from novy.infrastructure.session_security import SessionSecurity
from novy.infrastructure.settings import Settings, decode_session_key
from novy.infrastructure.supabase_auth import SupabaseAuthClient
from novy.infrastructure.supabase_jwt import SupabaseJwtValidator


def create_builder_session_service(
    database: AsyncDatabase[dict[str, Any]], settings: Settings
) -> BuilderSessionService:
    if settings.session_encryption_key is None or settings.session_lookup_key is None:
        raise RuntimeError("Builder session security is not configured")
    return BuilderSessionService(
        sessions=MongoBuilderSessionRepository(database[BUILDER_SESSIONS_COLLECTION]),
        profiles=MongoBuilderProfileRepository(database[BUILDER_PROFILES_COLLECTION]),
        protector=SessionSecurity(
            encryption_key=decode_session_key(settings.session_encryption_key),
            lookup_key=decode_session_key(settings.session_lookup_key),
            environment=settings.environment,
        ),
    )


def create_project_service(database: AsyncDatabase[dict[str, Any]]) -> ProjectService | None:
    if database is None:
        return None
    return ProjectService(
        MongoProjectRepository(database[PROJECTS_COLLECTION]),
    )


def create_csrf_service(settings: Settings) -> CsrfService | None:
    if settings.session_lookup_key is None:
        return None
    return CsrfService(
        lookup_key=decode_session_key(settings.session_lookup_key),
        environment=settings.environment,
    )


def create_builder_auth_service(
    *,
    settings: Settings,
    database: AsyncDatabase[dict[str, Any]],
    sessions: BuilderSessionService,
    http_client: httpx2.AsyncClient,
) -> BuilderAuthService | None:
    if settings.supabase_url is None or settings.supabase_publishable_key is None:
        return None
    base_url = settings.supabase_url.get_secret_value().strip()
    publishable_key = settings.supabase_publishable_key.get_secret_value().strip()
    provider = SupabaseAuthClient(
        base_url=base_url, publishable_key=publishable_key, http_client=http_client
    )
    validator = SupabaseJwtValidator(base_url=base_url, http_client=http_client)
    return BuilderAuthService(
        profiles=MongoBuilderProfileRepository(database[BUILDER_PROFILES_COLLECTION]),
        sessions=sessions,
        provider=provider,
        jwt_validator=validator,
    )
