from dataclasses import dataclass
from typing import Any

import httpx2
from pymongo.asynchronous.database import AsyncDatabase

from novy.application.builder_auth import BuilderAuthService
from novy.application.builder_sessions import BuilderSessionService
from novy.application.import_project import ImportService
from novy.application.projects import ProjectService
from novy.application.react_export import (
    EXPORT_RATE_LIMIT_OPERATION,
    ReactExportService,
)
from novy.application.snapshots import SnapshotService
from novy.application.template_provisioning import TemplateProvisioningService
from novy.infrastructure.app_data import (
    APP_COLLECTIONS_COLLECTION,
    APP_DATABASES_COLLECTION,
    APP_RECORDS_COLLECTION,
    MongoAppCollectionRepository,
    MongoAppDatabaseRepository,
    MongoAppRecordRepository,
)
from novy.infrastructure.builder_profiles import (
    BUILDER_PROFILES_COLLECTION,
    MongoBuilderProfileRepository,
)
from novy.infrastructure.builder_sessions import (
    BUILDER_SESSIONS_COLLECTION,
    MongoBuilderSessionRepository,
)
from novy.infrastructure.csrf import CsrfService
from novy.infrastructure.duplicate import MongoDuplicateRepository
from novy.infrastructure.import_project import MongoImportRepository
from novy.infrastructure.projects import (
    PROJECTS_COLLECTION,
    MongoProjectRepository,
)
from novy.infrastructure.rate_limit import RateLimiter
from novy.infrastructure.react_source.generator import ReactSourceGenerator
from novy.infrastructure.react_source.zip_archive import ZipArchiveWriter
from novy.infrastructure.session_security import SessionSecurity
from novy.infrastructure.settings import Settings, decode_session_key
from novy.infrastructure.snapshots import (
    SNAPSHOTS_COLLECTION,
    MongoSnapshotRepository,
)
from novy.infrastructure.supabase_auth import SupabaseAuthClient
from novy.infrastructure.supabase_jwt import SupabaseJwtValidator
from novy.infrastructure.template_provisioning import MongoTemplateProvisioningStore


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


@dataclass(frozen=True, slots=True)
class AppDataRepositories:
    databases: MongoAppDatabaseRepository
    collections: MongoAppCollectionRepository
    records: MongoAppRecordRepository


def create_app_data_repositories(
    database: AsyncDatabase[dict[str, Any]],
) -> AppDataRepositories | None:
    if database is None:
        return None
    return AppDataRepositories(
        databases=MongoAppDatabaseRepository(database[APP_DATABASES_COLLECTION]),
        collections=MongoAppCollectionRepository(database[APP_COLLECTIONS_COLLECTION]),
        records=MongoAppRecordRepository(database[APP_RECORDS_COLLECTION]),
    )


def create_project_service(database: AsyncDatabase[dict[str, Any]]) -> ProjectService | None:
    if database is None:
        return None
    return ProjectService(
        MongoProjectRepository(database[PROJECTS_COLLECTION]),
        duplicate_store=MongoDuplicateRepository(database),
    )


def create_template_provisioning_service(
    database: AsyncDatabase[dict[str, Any]],
) -> TemplateProvisioningService | None:
    if database is None:
        return None
    return TemplateProvisioningService(MongoTemplateProvisioningStore(database))


def create_import_service(database: AsyncDatabase[dict[str, Any]]) -> ImportService | None:
    if database is None:
        return None
    return ImportService(MongoImportRepository(database))


def create_snapshot_service(database: AsyncDatabase[dict[str, Any]]) -> SnapshotService | None:
    if database is None:
        return None
    return SnapshotService(
        MongoSnapshotRepository(database[SNAPSHOTS_COLLECTION]),
    )


def create_react_export_service(
    projects: ProjectService | None,
) -> ReactExportService:
    limiter = RateLimiter()
    limiter.configure(EXPORT_RATE_LIMIT_OPERATION, capacity=5, refill_rate=1 / 60)
    return ReactExportService(
        projects=projects,
        generator=ReactSourceGenerator(),
        archives=ZipArchiveWriter(),
        rate_limiter=limiter,
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
